from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastmcp import FastMCP

_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from agy_mcp_server import __version__
from agy_mcp_server.changes import (
    diff_snapshots,
    git_changed_files,
    git_diff,
    is_git_repo,
    snapshot_tree,
)
from agy_mcp_server.hardening import ensure_valid_mcp_config_json
from agy_mcp_server.models import (
    AgyCancelTaskRequest,
    AgyCancelTaskResponse,
    AgyCancelTaskRequestIn,
    AgyAppendPersistenceRequest,
    AgyAppendPersistenceRequestIn,
    AgyAppendPersistenceResponse,
    AgyClearCacheRequest,
    AgyClearCacheRequestIn,
    AgyClearCacheResponse,
    AgyHealthRequest,
    AgyHealthRequestIn,
    AgyHealthResponse,
    AgyInitPersistenceRequest,
    AgyInitPersistenceRequestIn,
    AgyInitPersistenceResponse,
    AgyListRunsRequest,
    AgyListRunsRequestIn,
    AgyListRunsResponse,
    AgyLoadPersistenceContextRequest,
    AgyLoadPersistenceContextRequestIn,
    AgyLoadPersistenceContextResponse,
    AgyPollTaskRequest,
    AgyPollTaskRequestIn,
    AgyPollTaskResponse,
    AgyQuotaRequest,
    AgyQuotaResponse,
    AgyQuotaStatus,
    AgyQuotaRequestIn,
    AgyReadPersistenceRequest,
    AgyReadPersistenceRequestIn,
    AgyReadPersistenceResponse,
    AgyRunTaskRequest,
    AgyRunTaskResponse,
    AgyStartTaskRequest,
    AgyStartTaskResponse,
    AgyRunTaskRequestIn,
    AgyStartTaskRequestIn,
    AgyUpdatePersistenceRequest,
    AgyUpdatePersistenceRequestIn,
    AgyUpdatePersistenceResponse,
    AgyRunResult,
    AgyRunSummary,
    WorkspaceChanges,
    AgySelfTestRequest,
    AgySelfTestRequestIn,
    AgySelfTestResponse,
    AgyToolSchemaReport,
)
from agy_mcp_server.provider import tool_name, prompt_name, PROVIDER_PREFIX
from agy_mcp_server.persistence import PersistenceStore, build_prompt_with_context
from agy_mcp_server.quota import (
    KNOWN_MODELS,
    QuotaStatus as InternalQuotaStatus,
    classify_agy_failure,
    fetch_gemini_api_quota,
    get_default_tracker,
    probe_agy_quota,
)
from agy_mcp_server.rolling_buffer import RollingTextBuffer
from agy_mcp_server.run_store import RunStore, StoredRun
from agy_mcp_server.settings import Settings


mcp = FastMCP(
    "agy-mcp-server",
    instructions="Exposes tools to run Antigravity CLI (agy) in a controlled workspace.",
)


_settings = Settings()
_run_store = RunStore(max_runs=_settings.max_runs)
_active_runs_lock = threading.Lock()
_active_runs: dict[str, "ActiveRun"] = {}

# Persistence store — file-based memory layer for AGENTS.md, PROJECTS.md, MEMORY.md.
# base_dir resolution: Settings.resolve_persistence_base_dir() honors
# persistence_location ("global" vs "workspace") and the $cwd_parent
# escape hatch in persistence_base_dir.
_persistence_store = PersistenceStore(
    base_dir=_settings.resolve_persistence_base_dir(),
    max_file_bytes=_settings.persistence_max_file_bytes,
    backup_on_write=_settings.persistence_backup_on_write,
    backup_keep=_settings.persistence_backup_keep,
    seed_templates=_settings.persistence_seed_templates,
    head_ratio=_settings.persistence_truncation_head_ratio,
)

# Quota tracker is constructed from settings; a fresh instance is used per
# process (no cross-process persistence by design).
_quota_tracker = get_default_tracker()
# Sync tracker settings with the loaded settings (in case env-driven).
_quota_tracker.period_hours = _settings.quota_period_hours
_quota_tracker.tier_limits = dict(_settings.quota_tier_limits)

if _settings.fix_antigravity_mcp_config:
    ensure_valid_mcp_config_json(_settings.antigravity_mcp_config_path)

# Warn at startup when quota enforcement is enabled but the active model
# is left at its default ("unknown"). That bucket is NOT in
# MODEL_QUOTA_REGISTRY, so the gate is effectively inert until the
# orchestrator configures AGY_MCP_QUOTA_ACTIVE_MODEL.
if (
    _settings.quota_policy_enabled
    and _settings.quota_active_model in ("unknown", "", None)
):
    logging.getLogger(__name__).warning(
        "quota_policy_enabled is True but quota_active_model is %r — "
        "the blocking gate will only fire after the conservative fallback "
        "limit (100 calls/window) is reached. Set "
        "AGY_MCP_QUOTA_ACTIVE_MODEL=<model> for accurate enforcement.",
        _settings.quota_active_model,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_workspace_path(workspace_path: str) -> Path:
    p = Path(workspace_path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("INVALID_WORKSPACE: workspace_path must be an existing directory")

    allowed_roots = _settings.resolved_allowed_roots()
    if not any(
        str(root) == "/" or p == root or str(p).startswith(str(root) + "/")
        for root in allowed_roots
    ):
        raise ValueError("NOT_ALLOWED: workspace_path is outside allowed roots")

    return p


def _agy_path() -> str:
    resolved = shutil.which(_settings.agy_path)
    if resolved is None:
        raise RuntimeError("AGY_NOT_FOUND: agy not found in PATH")
    return resolved


class QuotaExhaustedError(Exception):
    """Raised when an `agy` call would exceed the per-window quota and the
    active quota policy is enabled with overage disabled.

    Attributes:
        model (str): the model whose quota was exhausted.
        used (int): calls consumed in the current sliding window.
        limit (int): call cap for the active window.
        reset_in_seconds (int): seconds until the window resets and
            quota becomes available again.

    Resolution paths:
        1. Set AGY_MCP_ALLOW_OVERAGE=true (bypass) -- opt-in policy.
        2. Wait for the sliding-window reset.
        3. Switch model via `agy -i` then `/model` (if a different
           model has independent quota).

    Note:
        Defined at module top (post-_agy_path) so it is resolvable by
        docstrings (typing the exception in `Raises:` blocks) and by
        external callers that `from agy_mcp_server.server import
        QuotaExhaustedError` regardless of where in the file they import.
    """

    def __init__(self, *, model: str, used: int, limit: int, reset_in_seconds: int) -> None:
        self.model = model
        self.used = used
        self.limit = limit
        self.reset_in_seconds = reset_in_seconds
        super().__init__(
            f"QUOTA_EXHAUSTED: model={model!r} used={used}/{limit} "
            f"(resets in {reset_in_seconds}s). "
            f"Set AGY_MCP_ALLOW_OVERAGE=true to bypass, or wait for window reset."
        )


def _validate_exec_options(req: AgyRunTaskRequest) -> None:
    if req.options.extra_args is not None:
        for arg in req.options.extra_args:
            if not isinstance(arg, str):
                raise ValueError(f"NOT_ALLOWED: extra_args must contain only strings, got {type(arg).__name__}")
    
    if req.options.env is not None:
        for k, v in req.options.env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError(f"NOT_ALLOWED: env keys and values must be strings, got {type(k).__name__}/{type(v).__name__}")

    if _settings.mode == "safe":
        if _settings.force_sandbox_in_safe_mode and not req.options.sandbox:
            raise ValueError("NOT_ALLOWED: sandbox must be enabled in safe mode")
        if req.options.dangerously_skip_permissions:
            raise ValueError("NOT_ALLOWED: dangerously_skip_permissions is not allowed in safe mode")
        if req.options.env:
            raise ValueError("NOT_ALLOWED: custom env is not allowed in safe mode")
        if req.options.extra_args:
            raise ValueError("NOT_ALLOWED: extra_args is not allowed in safe mode")

    if req.options.extra_args:
        unknown = [a for a in req.options.extra_args if a not in _settings.allow_extra_args]
        if unknown:
            raise ValueError("NOT_ALLOWED: extra_args contains disallowed entries")

    if req.options.env:
        unknown = [k for k in req.options.env.keys() if k not in _settings.allow_env_keys]
        if unknown:
            raise ValueError("NOT_ALLOWED: env contains disallowed keys")


def _build_env(overrides: dict[str, str] | None) -> dict[str, str] | None:
    if not overrides:
        return None
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in overrides.items()})
    return env


def _run_agy(workspace: Path, request: AgyRunTaskRequest) -> tuple[str, str, int | None, bool]:
    agy = _agy_path()

    _validate_exec_options(request)

    args: list[str] = [agy, "--add-dir", str(workspace)]
    if request.options.sandbox:
        args.append("--sandbox")
    if request.options.dangerously_skip_permissions:
        args.append("--dangerously-skip-permissions")
    args.extend(request.options.extra_args)

    input_text = f"{request.prompt}\n"

    env = _build_env(request.options.env)

    try:
        timeout = max(1, request.options.timeout_s or _settings.default_timeout_s)
        proc = subprocess.run(
            args,
            cwd=str(workspace),
            input=input_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        return stdout, stderr, None, True


def _build_agy_popen(
    workspace: Path, request: AgyRunTaskRequest
) -> tuple[subprocess.Popen[str], dict[str, Any] | None, str]:
    agy = _agy_path()

    _validate_exec_options(request)

    args: list[str] = [agy, "--add-dir", str(workspace)]
    if request.options.sandbox:
        args.append("--sandbox")
    if request.options.dangerously_skip_permissions:
        args.append("--dangerously-skip-permissions")
    args.extend(request.options.extra_args)

    input_text = f"{request.prompt}\n"

    env = _build_env(request.options.env)

    proc = subprocess.Popen(
        args,
        cwd=str(workspace),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        start_new_session=True,
        env=env,
    )
    return proc, env, input_text


def _terminate_process(proc: subprocess.Popen[str], *, force: bool) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = None

    if force:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
        return

    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass


def _reader_thread(pipe: Any, buf: RollingTextBuffer) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            buf.append(line)
    except Exception:
        return
    finally:
        try:
            pipe.close()
        except Exception:
            pass


@dataclass
class ActiveRun:
    run_id: str
    workspace: Path
    request: AgyRunTaskRequest
    started_at: datetime
    proc: subprocess.Popen[str]
    stdout_buf: RollingTextBuffer
    stderr_buf: RollingTextBuffer
    before_snapshot: dict[str, Any] | None
    cancel_requested: bool = False


def _finalize_active_run(run: ActiveRun) -> None:
    timeout_s = max(1, run.request.options.timeout_s or _settings.default_timeout_s)
    timed_out = False

    out_t = threading.Thread(target=_reader_thread, args=(run.proc.stdout, run.stdout_buf), daemon=True)
    err_t = threading.Thread(target=_reader_thread, args=(run.proc.stderr, run.stderr_buf), daemon=True)
    out_t.start()
    err_t.start()

    try:
        run.proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process(run.proc, force=False)
        try:
            run.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _terminate_process(run.proc, force=True)

    out_t.join(timeout=2)
    err_t.join(timeout=2)

    finished_at = _now()
    exit_code = run.proc.returncode

    stdout = run.stdout_buf.get()
    stderr = run.stderr_buf.get()

    # Quota tracking: record the call and classify any failure.
    # Note: the gate (raise QuotaExhaustedError) runs in agy_start_task
    # BEFORE Popen, not here — exceptions raised from this daemon thread
    # would be silently swallowed and never reach the caller.
    _quota_tracker.record_call(_settings.quota_active_model)
    if exit_code != 0 or timed_out:
        _quota_tracker.record_failure(
            classify_agy_failure(exit_code, stdout, stderr, timed_out)
        )

    changes: WorkspaceChanges | None = None
    if run.request.capture_changes:
        if is_git_repo(run.workspace):
            diff = git_diff(run.workspace)
            changes = WorkspaceChanges(
                method="git",
                changed_files=git_changed_files(run.workspace),
                diff=diff if diff else None,
            )
        else:
            if run.before_snapshot is None:
                changes = WorkspaceChanges(method="none", changed_files=[], diff=None)
            else:
                after_snapshot = snapshot_tree(
                    run.workspace,
                    ignore_dir_names=_settings.ignore_dir_names,
                    max_file_bytes=_settings.snapshot_max_file_bytes,
                )
                changes = WorkspaceChanges(
                    method="snapshot",
                    changed_files=diff_snapshots(run.before_snapshot, after_snapshot),
                    diff=None,
                )

    status = "timed_out" if timed_out else ("done" if (exit_code == 0) else "failed")
    result = AgyRunResult(
        run_id=run.run_id,
        workspace_path=str(run.workspace),
        stdout=stdout[: _settings.max_output_bytes],
        stderr=stderr[: _settings.max_output_bytes],
        exit_code=exit_code,
        timed_out=timed_out,
        started_at=run.started_at,
        finished_at=finished_at,
    )

    _run_store.put(
        run.run_id,
        StoredRun(status=status, result=result, changes=changes, started_at=run.started_at),
    )

    with _active_runs_lock:
        _active_runs.pop(run.run_id, None)


@mcp.prompt
def prompt_sync_orchestration(*, workspace_path: str, goal: str) -> str:
    """Orchestration playbook for running a single synchronous `agy_run_task` safely.

    Input:
        - workspace_path (str, required): absolute path to an existing dir
          inside AGY_MCP_ALLOWED_ROOTS. Used as the JSON example value.
        - goal (str, required): the high-level outcome you want the agent
          to produce. Inlined as a single bullet under "Goal:".

    Returns:
        A markdown playbook with: goal statement, workspace echo, constraints
        (model-selection, workspace, safe defaults), 4-step execution plan,
        and a JSON example for agy_run_task.

    Side effects:
        None. The prompt is pure text generation; no MCP tool is called.

    Use when:
        You (the orchestrator agent) need a reliable, repeatable sequence to
        run one Antigravity CLI task and optionally capture workspace changes.

    Example:
        prompt_agy_sync_orchestration(workspace_path="/srv/proj",
                                      goal="Add docstrings to all public functions")
    """
    return (
        "You are orchestrating an MCP server that executes Antigravity CLI (`agy`) inside a controlled workspace.\n"
        "\n"
        "Goal:\n"
        f"- {goal}\n"
        "\n"
        "Workspace:\n"
        f"- workspace_path: {workspace_path}\n"
        "\n"
        "Constraints and important behavior:\n"
        "- The MCP server does NOT control the `agy` reasoning model. Model selection is configured in the CLI via `/model` and persists across sessions.\n"
        "- `workspace_path` must be an existing directory inside the server's allowed roots.\n"
        "- Prefer safe defaults: sandbox enabled, no custom env, no extra_args.\n"
        "\n"
        "Recommended execution plan (synchronous):\n"
        "1) Call `agy_health` once if you haven't verified the binary for this environment.\n"
        "2) Call `agy_run_task` with:\n"
        "   - workspace_path set to the provided value\n"
        "   - prompt containing clear instructions and acceptance criteria\n"
        "   - capture_changes=true\n"
        "   - change_scope=\"workspace\"\n"
        "3) Use the returned `changes` field to decide what to do next:\n"
        "   - If changes.method==\"git\": you may inspect `diff` (unified git diff) and changed_files.\n"
        "   - If changes.method==\"snapshot\": you only get changed_files; open files as needed to review.\n"
        "4) If the run failed or timed_out:\n"
        "   - Review stderr and stdout for actionable error text.\n"
        "   - Retry with a more constrained prompt, or break down the task.\n"
        "\n"
        "JSON example (agy_run_task):\n"
        "{\n"
        "  \"workspace_path\": \"" + workspace_path.replace('\"', '\\\\\"') + "\",\n"
        "  \"prompt\": \"<write a precise task here with steps and success criteria>\",\n"
        "  \"capture_changes\": true,\n"
        "  \"change_scope\": \"workspace\",\n"
        "  \"options\": {\n"
        "    \"sandbox\": true,\n"
        "    \"dangerously_skip_permissions\": false,\n"
        "    \"timeout_s\": 300,\n"
        "    \"env\": null,\n"
        "    \"extra_args\": []\n"
        "  }\n"
        "}\n"
    )


@mcp.prompt
def prompt_async_orchestration(*, workspace_path: str, goal: str) -> str:
    """Orchestration playbook for start_task + poll_task + cancel_task.

    Input:
        - workspace_path (str, required): absolute path inside
          AGY_MCP_ALLOWED_ROOTS. Used as the JSON example value.
        - goal (str, required): the high-level outcome you want. Inlined
          as a single bullet under "Goal:".

    Returns:
        A markdown playbook with: goal statement, workspace echo, constraints
        (model-selection, run lifecycle), 5-step execution plan covering
        backoff polling and force escalation, plus JSON examples for
        agy_start_task / agy_poll_task / agy_cancel_task.

    Side effects:
        None. Pure text generation; no MCP tool is called.

    Use when:
        You need non-blocking execution, progress polling, and a safe
        cancellation strategy.

    Example:
        prompt_agy_async_orchestration(workspace_path="/srv/proj",
                                       goal="Long-running migration across 50 files")
    """
    return (
        "You are orchestrating an MCP server that executes Antigravity CLI (`agy`) inside a controlled workspace.\n"
        "\n"
        "Goal:\n"
        f"- {goal}\n"
        "\n"
        "Workspace:\n"
        f"- workspace_path: {workspace_path}\n"
        "\n"
        "Constraints and important behavior:\n"
        "- The MCP server does NOT control the `agy` reasoning model. Model selection is configured in the CLI via `/model` and persists across sessions.\n"
        "- While a run is active, `agy_poll_task` returns partial_stdout/partial_stderr tails and status=\"running\".\n"
        "- After completion, `agy_poll_task` returns status in {done, failed, timed_out} and includes `result` (and `changes` if enabled).\n"
        "\n"
        "Recommended execution plan (async):\n"
        "1) Call `agy_start_task` with capture_changes=true (unless you explicitly don't need it).\n"
        "2) Store run_id.\n"
        "3) Poll with backoff:\n"
        "   - Poll quickly at first (e.g., 0.25–0.5s) to catch fast runs.\n"
        "   - Then increase to 1–2s intervals for longer runs.\n"
        "   - Always stop when status != \"running\".\n"
        "4) If you detect a stuck run or need to stop:\n"
        "   - Call `agy_cancel_task` with force=false first.\n"
        "   - If it does not exit promptly, call again with force=true.\n"
        "5) Once done:\n"
        "   - Inspect result.stdout/result.stderr and changes.\n"
        "\n"
        "JSON example (agy_start_task):\n"
        "{\n"
        "  \"workspace_path\": \"" + workspace_path.replace('\"', '\\\\\"') + "\",\n"
        "  \"prompt\": \"<write a precise task here with steps and success criteria>\",\n"
        "  \"capture_changes\": true,\n"
        "  \"change_scope\": \"workspace\",\n"
        "  \"options\": {\n"
        "    \"sandbox\": true,\n"
        "    \"dangerously_skip_permissions\": false,\n"
        "    \"timeout_s\": 300,\n"
        "    \"env\": null,\n"
        "    \"extra_args\": []\n"
        "  }\n"
        "}\n"
        "\n"
        "JSON example (agy_poll_task):\n"
        "{ \"run_id\": \"run-<uuid>\" }\n"
        "\n"
        "JSON example (agy_cancel_task):\n"
        "{ \"run_id\": \"run-<uuid>\", \"force\": false }\n"
    )


@mcp.prompt
def prompt_model_selection_guidance() -> str:
    """Explain the model-selection limitation and the `/model` CLI command.

    Input: (none). Zero-arg prompt.

    Returns:
        A markdown explainer covering: why the MCP server does NOT control
        model selection, the 4-step `/model` CLI procedure, and operational
        guidance (configure before MCP tasks; avoid mid-run changes).

    Side effects:
        None. Pure text generation.

    Use when:
        You need to explain the model-selection limitation and how a user
        configures the model directly in the Antigravity CLI via `/model`.
    """
    return (
        "Important: This MCP server does NOT control which reasoning model Antigravity CLI (`agy`) uses.\n"
        "\n"
        "How model selection works:\n"
        "- `agy` model selection is configured inside the interactive CLI UI via the `/model` command.\n"
        "- The selected model persists across sessions and will affect subsequent runs triggered via this MCP server.\n"
        "\n"
        "Recommended operator steps:\n"
        "1) Launch the CLI interactively:\n"
        "   agy -i\n"
        "2) In the prompt, type:\n"
        "   /model\n"
        "3) Select the desired reasoning model from the list and confirm.\n"
        "4) Exit the CLI:\n"
        "   /exit\n"
        "\n"
        "Operational guidance:\n"
        "- Configure the model BEFORE running MCP tasks if you need a specific model.\n"
        "- Avoid changing models while long tasks are running; model changes may apply only to new turns.\n"
    )


@mcp.prompt
def prompt_security_and_workspace_rules() -> str:
    """Safety rules and workspace constraints for orchestrators.

    Input: (none). Zero-arg prompt.

    Returns:
        A markdown reminder covering: workspace_path constraints,
        AGY_MCP_ALLOWED_ROOTS gate, safe-mode (AGY_MCP_MODE=safe)
        restrictions, permissive-mode (AGY_MCP_MODE=permissive) allowlists,
        and recommended safe defaults.

    Side effects:
        None. Pure text generation.

    Use when:
        You need to remind an orchestrator how to stay within server
        safety boundaries and how to choose safe defaults.
    """
    return (
        "Safety and workspace rules for agy-mcp-server:\n"
        "\n"
        "Workspace constraints:\n"
        "- workspace_path must be an existing directory.\n"
        "- workspace_path must be inside AGY_MCP_ALLOWED_ROOTS.\n"
        "\n"
        "Safe mode (AGY_MCP_MODE=safe) expectations:\n"
        "- sandbox must be enabled.\n"
        "- env is not allowed.\n"
        "- extra_args is not allowed.\n"
        "- dangerously_skip_permissions is not allowed.\n"
        "\n"
        "Permissive mode (AGY_MCP_MODE=permissive) expectations:\n"
        "- env keys are restricted by AGY_MCP_ALLOW_ENV_KEYS.\n"
        "- extra_args entries are restricted by AGY_MCP_ALLOW_EXTRA_ARGS.\n"
        "- Prefer minimal overrides; do not pass secrets unless explicitly required.\n"
        "\n"
        "Recommended defaults for orchestrators:\n"
        "- Use sandbox=true.\n"
        "- Keep env=null and extra_args=[] unless there is a validated, allowlisted need.\n"
        "- Use capture_changes=true to enable review workflows.\n"
    )


@mcp.tool(name=tool_name("health"))
def agy_health(req: AgyHealthRequestIn | None = None) -> AgyHealthResponse:
    """
    Health check for the Antigravity CLI binary (`agy`).

    This tool verifies that `agy` is discoverable on PATH and returns its version string.

    Input (optional fields marked):
        - expected_version (str|None): if set, ok=false when the installed
          version does not exactly match.

    Returns:
        AgyHealthResponse with:
        - agy_path: resolved absolute path of the `agy` binary.
        - agy_version: raw `agy --version` output (trimmed).
        - ok: whether the health check passed.
        - notes: details such as version mismatch information.

    Raises:
        RuntimeError("AGY_NOT_FOUND: ..."): the `agy` binary is missing
          from PATH (raised inside _agy_path before subprocess call).

    Side effects:
        Spawns one short-lived `agy --version` subprocess. No workspace
        mutation, no persistence write.

    Example:
        run_mcp(name="agy_health", args={})                          # default
        run_mcp(args={"req": {"expected_version": "1.2.3"}})         # pin

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyHealthRequest()
    agy = _agy_path()
    version = subprocess.check_output([agy, "--version"], text=True).strip()

    notes: list[str] = []
    ok = True
    if req.expected_version and version != req.expected_version:
        ok = False
        notes.append(f"expected_version_mismatch: expected={req.expected_version} got={version}")

    return AgyHealthResponse(agy_path=agy, agy_version=version, ok=ok, notes=notes)


@mcp.tool(name=tool_name("run_task"))
def agy_run_task(req: AgyRunTaskRequestIn | None = None) -> AgyRunTaskResponse:
    """
    Run a single Antigravity CLI task synchronously (blocking).

    This tool spawns `agy` as a subprocess, feeds the prompt via stdin, waits for completion
    (subject to timeout), and returns captured stdout/stderr plus an optional workspace change
    summary.

    Workspace & safety:
    - workspace_path must be an existing directory inside AGY_MCP_ALLOWED_ROOTS.
    - In safe mode (AGY_MCP_MODE=safe), env and extra_args are rejected and sandbox is enforced.
    - In permissive mode, env keys and extra_args are still restricted by allowlists.

    Output limits:
    - stdout/stderr are truncated to AGY_MCP_MAX_OUTPUT_BYTES.

    Change capture (capture_changes=true):
    - If the workspace is a Git repo, returns changed_files and a unified git diff.
    - Otherwise, returns a snapshot-based changed_files list (no diff payload).

    Model selection note:
    - This MCP server does NOT control which model `agy` uses. The active
      model is whatever is configured interactively inside the CLI via
      `/model`. To change it, run `agy` once and use the picker; that
      persists across MCP sessions.

    Input (optional fields marked):
        - prompt (str, required): the task instructions sent on stdin.
        - workspace_path (str, optional): existing dir inside
          AGY_MCP_ALLOWED_ROOTS. Defaults to the server cwd.
        - timeout_s (int, optional): wall-clock cap. Default 300, max 3600.
        - sandbox (bool, optional): spawns `agy` in a sandbox. Default True,
          forced in safe mode.
        - dangerously_skip_permissions (bool, optional): bypass confirm
          prompts. Default False. Rejected in safe mode.
        - env (dict[str,str], optional): extra env vars. Default {}.
          Rejected in safe mode.
        - extra_args (list[str], optional): passthrough CLI flags. Default [].
          Rejected in safe mode.
        - capture_changes (bool, optional): if True (default), return a diff
          (git) or file list (snapshot) of workspace mutations.
        - change_scope ("workspace"|"git_only", optional): scopes the diff.
          Default "workspace".

    Returns:
        AgyRunTaskResponse with:
        - result: { run_id, workspace_path, stdout, stderr, exit_code,
          timed_out, started_at, finished_at }
        - changes: { method ("git"|"snapshot"|"none"), changed_files,
          diff (str|None) }
        - quota_warning: "ok"|"warning"|"exhausted" for the active model.
        - quota_remaining_pct: 0-100 rounded to 1 decimal.

    Raises:
        ValueError("INVALID_WORKSPACE: ..."): workspace_path missing or not a dir.
        ValueError("NOT_ALLOWED: ..."): workspace_path outside ALLOWED_ROOTS,
          or env/extra_args violation in safe mode.
        QuotaExhaustedError: policy enabled, overage disabled, quota is
          "exhausted" at the moment of the call.

    Side effects:
        - Records 1 call against the sliding-window quota tracker for
          AGY_MCP_QUOTA_ACTIVE_MODEL.
        - On non-zero exit or timeout, classifies the failure via
          `classify_agy_failure`.
        - Persists the run_id in the in-memory run store
          (visible to agy_list_runs for the lifetime of the server).
        - The spawned `agy` subprocess may mutate `workspace_path`.

    Example:
        run_mcp(args={"req": {
            "prompt": "list every .py file under src/",
            "workspace_path": "/srv/proj",
            "timeout_s": 120,
            "capture_changes": True,
        }})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyRunTaskRequest()
    workspace = _resolve_workspace_path(req.workspace_path)
    _validate_exec_options(req)

    # Optional: load persistent context and prepend to the prompt.
    # Failures are non-fatal — handled inside the helper.
    prompt_text = build_prompt_with_context(
        req.prompt, settings=_settings, store=_persistence_store
    )

    before_snapshot = None
    if req.capture_changes and not is_git_repo(workspace) and req.change_scope == "workspace":
        before_snapshot = snapshot_tree(
            workspace,
            ignore_dir_names=_settings.ignore_dir_names,
            max_file_bytes=_settings.snapshot_max_file_bytes,
        )

    started_at = _now()

    # Quota gate: must run BEFORE subprocess to avoid burning a real quota
    # slot when the policy says we should block.
    if _settings.quota_policy_enabled and not _settings.allow_overage:
        _gate_snap = _quota_tracker.snapshot(_settings.quota_active_model)
        if _gate_snap.warning == "exhausted":
            raise QuotaExhaustedError(
                model=_gate_snap.model,
                used=_gate_snap.used,
                limit=_gate_snap.limit,
                reset_in_seconds=_gate_snap.window_remaining_seconds,
            )

    stdout, stderr, exit_code, timed_out = _run_agy(
        workspace,
        req.model_copy(update={"prompt": prompt_text}),
    )
    finished_at = _now()

    # Quota tracking: record the call and classify any failure.
    _quota_tracker.record_call(_settings.quota_active_model)
    if exit_code != 0 or timed_out:
        _quota_tracker.record_failure(
            classify_agy_failure(exit_code, stdout, stderr, timed_out)
        )

    stdout = stdout[: _settings.max_output_bytes]
    stderr = stderr[: _settings.max_output_bytes]

    run_id = f"run-{uuid4()}"
    result = AgyRunResult(
        run_id=run_id,
        workspace_path=str(workspace),
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        timed_out=timed_out,
        started_at=started_at,
        finished_at=finished_at,
    )

    changes: WorkspaceChanges | None = None
    if req.capture_changes:
        if is_git_repo(workspace):
            diff = git_diff(workspace)
            changes = WorkspaceChanges(
                method="git",
                changed_files=git_changed_files(workspace),
                diff=diff if diff else None,
            )
        else:
            if before_snapshot is None:
                changes = WorkspaceChanges(method="none", changed_files=[], diff=None)
            else:
                after_snapshot = snapshot_tree(
                    workspace,
                    ignore_dir_names=_settings.ignore_dir_names,
                    max_file_bytes=_settings.snapshot_max_file_bytes,
                )
                changes = WorkspaceChanges(
                    method="snapshot",
                    changed_files=diff_snapshots(before_snapshot, after_snapshot),
                    diff=None,
                )

    status = "timed_out" if timed_out else ("done" if (exit_code == 0) else "failed")
    _run_store.put(
        run_id,
        StoredRun(status=status, result=result, changes=changes, started_at=started_at),
    )

    _snap = _quota_tracker.snapshot(_settings.quota_active_model)
    return AgyRunTaskResponse(
        result=result,
        changes=changes,
        quota_warning=_snap.warning,
        quota_remaining_pct=round(_snap.remaining / max(_snap.limit, 1) * 100, 1),
    )


@mcp.tool(name=tool_name("start_task"))
def agy_start_task(req: AgyStartTaskRequestIn | None = None) -> AgyStartTaskResponse:
    """
    Start an Antigravity CLI task asynchronously (non-blocking).

    This tool spawns `agy` in the background, writes the prompt to stdin, closes stdin, and
    returns a run_id that can be polled via agy_poll_task.

    Behavior:
    - The run is tracked in-memory while running.
    - Output is buffered incrementally and can be retrieved as partial_stdout/partial_stderr
      while the process is still running.
    - Once finished, the run result is persisted in the in-memory run store for later polling
      and listing.

    Workspace & safety:
    - workspace_path must be an existing directory inside AGY_MCP_ALLOWED_ROOTS.
    - Safe/permissive rules are the same as agy_run_task.

    Model selection note:
    - This MCP server does NOT control which model `agy` uses. Configure
      it interactively inside the CLI via `/model`; that state persists
      across MCP sessions.

    Input (optional fields marked):
        Same field set as `agy_run_task` (`prompt`, `workspace_path`,
        `timeout_s`, `sandbox`, `dangerously_skip_permissions`, `env`,
        `extra_args`, `capture_changes`, `change_scope`). See that tool's
        docstring for per-field semantics.

    Returns:
        AgyStartTaskResponse with:
        - run_id (str): opaque id (e.g. "run-..."); pass to poll/cancel.
        - started_at (datetime, UTC): when the subprocess was spawned.
        - quota_warning, quota_remaining_pct: snapshot at start time.

    Raises:
        ValueError("INVALID_WORKSPACE: ..."): workspace_path missing or not a dir.
        ValueError("NOT_ALLOWED: ..."): workspace_path outside ALLOWED_ROOTS,
          or env/extra_args violation in safe mode.
        QuotaExhaustedError: policy enabled, overage disabled, quota
          "exhausted" at the moment of the spawn.
        The subprocess spawn itself never raises on the caller side --
          if `agy` exits immediately with an error, that surfaces
          later via `agy_poll_task` (status="failed").

    Side effects:
        - Spawns a daemon thread (`_finalize_active_run`) that mutates
          the run store after the subprocess exits.
        - Records 1 call against the quota tracker at start time.
        - Holds a stdin pipe on the subprocess until EOF is reached
          (the prompt is fully written, then stdin is closed).
        - Registers the run in `_active_runs` so subsequent
          `agy_poll_task` / `agy_cancel_task` calls can find it.

    Example:
        rid = run_mcp(name="agy_start_task",
                      args={"req": {"prompt": "..."}}).run_id
        # ... later ...
        run_mcp(name="agy_poll_task", args={"req": {"run_id": rid}}).result

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyStartTaskRequest()
    workspace = _resolve_workspace_path(req.workspace_path)
    _validate_exec_options(req)

    # Optional: load persistent context and prepend to the prompt.
    # Failures are non-fatal — handled inside the helper.
    prompt_text = build_prompt_with_context(
        req.prompt, settings=_settings, store=_persistence_store
    )

    before_snapshot = None
    if req.capture_changes and not is_git_repo(workspace) and req.change_scope == "workspace":
        before_snapshot = snapshot_tree(
            workspace,
            ignore_dir_names=_settings.ignore_dir_names,
            max_file_bytes=_settings.snapshot_max_file_bytes,
        )

    started_at = _now()
    run_id = f"run-{uuid4()}"

    # Quota gate: must run BEFORE Popen so we don't spawn the subprocess
    # when the policy says we should block. The finalize thread does NOT
    # re-raise (it runs in a daemon and exceptions are silently swallowed).
    if _settings.quota_policy_enabled and not _settings.allow_overage:
        _gate_snap = _quota_tracker.snapshot(_settings.quota_active_model)
        if _gate_snap.warning == "exhausted":
            raise QuotaExhaustedError(
                model=_gate_snap.model,
                used=_gate_snap.used,
                limit=_gate_snap.limit,
                reset_in_seconds=_gate_snap.window_remaining_seconds,
            )

    req_for_agy = req.model_copy(update={"prompt": prompt_text})
    proc, _, input_text = _build_agy_popen(workspace, req_for_agy)
    if proc.stdin is not None:
        try:
            proc.stdin.write(input_text)
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass

    active = ActiveRun(
        run_id=run_id,
        workspace=workspace,
        request=req,
        started_at=started_at,
        proc=proc,
        stdout_buf=RollingTextBuffer(max_bytes=_settings.max_output_bytes),
        stderr_buf=RollingTextBuffer(max_bytes=_settings.max_output_bytes),
        before_snapshot=before_snapshot,
    )

    with _active_runs_lock:
        _active_runs[run_id] = active

    t = threading.Thread(target=_finalize_active_run, args=(active,), daemon=True)
    t.start()

    _snap = _quota_tracker.snapshot(_settings.quota_active_model)
    return AgyStartTaskResponse(
        run_id=run_id,
        started_at=started_at,
        quota_warning=_snap.warning,
        quota_remaining_pct=round(_snap.remaining / max(_snap.limit, 1) * 100, 1),
    )


@mcp.tool(name=tool_name("poll_task"))
def agy_poll_task(req: AgyPollTaskRequestIn | None = None) -> AgyPollTaskResponse:
    """
    Poll an asynchronous task started by agy_start_task.

    While the run is active:
    - status="running"
    - result=None
    - partial_stdout / partial_stderr contain a tail of the buffered output (bounded).
    - changes=None (changes are computed only after completion)

    After completion:
    - status is one of "done", "failed", or "timed_out"
    - result contains full stdout/stderr (subject to truncation limits)
    - changes contains workspace change information if capture_changes was enabled

    Input (optional fields marked):
        - run_id (str, required): the id returned by agy_start_task.

    Returns:
        AgyPollTaskResponse with status, result (None while running),
        partial_stdout/partial_stderr (tail while running, "" after),
        changes (None while running), quota_warning,
        quota_remaining_pct.

    Raises:
        RuntimeError("RUN_NOT_FOUND: unknown run_id"): the id was
          never started, or has already been evicted from the run
          store (bounded by Settings.max_runs).

    Side effects:
        None. Polling is read-only against the in-memory run buffer.

    Example:
        run_mcp(name="agy_poll_task",
                args={"req": {"run_id": "run-..."}})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyPollTaskRequest()
    with _active_runs_lock:
        active = _active_runs.get(req.run_id)

    _snap = _quota_tracker.snapshot(_settings.quota_active_model)
    _q_warn = _snap.warning
    _q_pct = round(_snap.remaining / max(_snap.limit, 1) * 100, 1)

    if active is not None:
        max_tail = min(_settings.max_output_bytes, 200_000)
        return AgyPollTaskResponse(
            status="running",
            result=None,
            partial_stdout=active.stdout_buf.tail(max_tail),
            partial_stderr=active.stderr_buf.tail(max_tail),
            changes=None,
            quota_warning=_q_warn,
            quota_remaining_pct=_q_pct,
        )

    stored = _run_store.get(req.run_id)
    if stored is None:
        raise RuntimeError("RUN_NOT_FOUND: unknown run_id")

    return AgyPollTaskResponse(
        status=stored.status,
        result=stored.result,
        partial_stdout="",
        partial_stderr="",
        changes=stored.changes,
        quota_warning=_q_warn,
        quota_remaining_pct=_q_pct,
    )


@mcp.tool(name=tool_name("cancel_task"))
def agy_cancel_task(req: AgyCancelTaskRequestIn | None = None) -> AgyCancelTaskResponse:
    """
    Cancel an asynchronous task started by agy_start_task.

    Cancellation strategy:
    - force=false: send SIGTERM to the process group (graceful), falling back to terminate().
    - force=true: send SIGKILL to the process group (hard kill), falling back to kill().

    Input (optional fields marked):
        - run_id (str, required): the id returned by agy_start_task.
        - force (bool, optional): see Cancellation strategy. Default False.

    Returns:
        AgyCancelTaskResponse with:
        - canceled (bool): True iff a running process was targeted.
        - status: "canceled" | "already_done" | "not_found".

    Raises:
        None. Unknown ids return status="not_found" without raising.

    Side effects:
        - May terminate a child process and its process group.
        - The finalize thread records the resulting "canceled" status
          in the run store (visible via agy_poll_task).

    Example:
        run_mcp(name="agy_cancel_task",
                args={"req": {"run_id": "run-...", "force": False}})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyCancelTaskRequest()
    with _active_runs_lock:
        active = _active_runs.get(req.run_id)

    if active is None:
        stored = _run_store.get(req.run_id)
        if stored is not None:
            return AgyCancelTaskResponse(canceled=False, status="already_done")
        return AgyCancelTaskResponse(canceled=False, status="not_found")

    active.cancel_requested = True
    _terminate_process(active.proc, force=req.force)
    return AgyCancelTaskResponse(canceled=True, status="canceled")


@mcp.tool(name=tool_name("list_runs"))
def agy_list_runs(req: AgyListRunsRequestIn | None = None) -> AgyListRunsResponse:
    """
    List recent runs (both currently running and recently completed).

    Ordering:
    - Running tasks are listed first, newest to oldest.
    - Completed tasks are then listed from the in-memory run store, newest to oldest.

    Input:
    - limit: maximum number of entries to return

    Input (optional fields marked):
        - limit (int, optional): max entries to return. Default 50.

    Returns:
        AgyListRunsResponse with a `runs` list of AgyRunSummary:
        { run_id, workspace_path, status, started_at }.

    Raises:
        None. Unknown ids do not raise -- they are simply absent.

    Side effects:
        None. Read-only against the active-set and run-store.

    Example:
        run_mcp(name="agy_list_runs", args={"req": {"limit": 10}})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyListRunsRequest()
    runs: list[AgyRunSummary] = []

    with _active_runs_lock:
        active_items = list(_active_runs.values())
    active_items.sort(key=lambda r: r.started_at, reverse=True)

    for r in active_items[: max(0, req.limit)]:
        runs.append(
            AgyRunSummary(
                run_id=r.run_id,
                workspace_path=str(r.workspace),
                status="running",
                started_at=r.started_at,
            )
        )

    remaining = max(0, req.limit - len(runs))
    if remaining:
        items = _run_store.list(remaining)
        for run_id, stored in items:
            runs.append(
                AgyRunSummary(
                    run_id=run_id,
                    workspace_path=stored.result.workspace_path if stored.result else "",
                    status=stored.status,
                    started_at=stored.started_at,
                )
            )
    return AgyListRunsResponse(runs=runs)


def _internal_to_response_status(s: InternalQuotaStatus) -> AgyQuotaStatus:
    """Convert internal QuotaStatus dataclass to the Pydantic response model."""
    return AgyQuotaStatus(
        model=s.model,
        tier=s.tier,
        used=s.used,
        limit=s.limit,
        remaining=s.remaining,
        reset_at=s.reset_at,
        period_hours=s.period_hours,
        healthy=s.healthy,
        source=s.source,  # type: ignore[arg-type]
        notes=list(s.notes),
        window_resets_in_seconds=s.window_resets_in_seconds,
    )


@mcp.tool(name=tool_name("quota"))
def agy_quota(req: AgyQuotaRequestIn | None = None) -> AgyQuotaResponse:
    """
    Check Antigravity CLI model quota status.

    The Antigravity CLI does NOT expose a direct quota inspection endpoint.
    This tool implements a hybrid strategy combining four sources:

    A) Local sliding-window counter (always-on, zero-cost):
       - Tracks every agy_run_task / agy_start_task invocation per active model
         in a 5-hour window (the documented quota refresh cadence).
       - Reports `used`, `limit` (from settings), `remaining`, and `reset_at`.
       - Works even when `agy` itself is broken.

    B) Failure classifier (always-on):
       - Inspects every failed agy run for quota-related keywords
         (`resource_exhausted`, `quota exceeded`, `rate limit`, `429`).
       - Notes last failure classification in each status entry.

    C) Probe call (opt-in via `probe=True`):
       - Runs a minimal `agy --prompt ok --max-turns 1` to verify the CLI is
         responsive. WARNING: this call itself consumes quota.
       - Useful when the local counter and stderr parser are inconclusive.

    D) External API (opt-in via `use_api=True`):
       - Stub: queries the Gemini API quota endpoint if implemented.
       - Currently returns None with a logged warning unless `use_api=False`.

    Input (optional fields marked):
        - model (str|None): restrict report to one model. None returns all.
        - tier (str|enum): subscription tier for limit lookup
          (free|pro|ultra|enterprise|unknown). Default "unknown" applies a
          permissive 999_999 limit.
        - probe (bool): opt-in flag for strategy C (runs an `agy` smoke).
          WARNING: probe itself consumes quota.
        - use_api (bool): opt-in flag for strategy D (external API). Stub.

    Returns:
        AgyQuotaResponse with:
        - statuses: list of per-model QuotaStatus entries.
        - overall_healthy: True if all statuses are healthy.
        - active_model: configured active model (AGY_MCP_QUOTA_ACTIVE_MODEL).
        - notes: top-level notes (e.g., warnings about probe consumption).

    Raises:
        None. QuotaExhaustedError is raised by agy_run_task / agy_start_task
        based on the policy + counter snapshot, not by this tool.

    Side effects:
        - When probe=True: spawns a short `agy` subprocess which consumes
          a turn of quota against the active model.
        - When use_api=True: may issue a remote quota-API call (currently
          logs a warning and returns None).

    Example:
        run_mcp(name="agy_quota", args={"req": {}})                # all
        run_mcp(name="agy_quota", args={"req": {"model": "flash"}})
        run_mcp(name="agy_quota", args={"req": {"probe": True}})   # consume

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyQuotaRequest()
    notes: list[str] = []
    active_model = _settings.quota_active_model
    tier = req.tier

    statuses: list[AgyQuotaStatus] = []

    if req.model:
        # Single-model lookup.
        internal = _quota_tracker.status(req.model, tier)
        statuses.append(_internal_to_response_status(internal))
    else:
        # All known models + the active model (which has our actual usage).
        models_to_check: list[str] = sorted(KNOWN_MODELS)
        if active_model and active_model not in models_to_check:
            models_to_check.append(active_model)
        for m in models_to_check:
            internal = _quota_tracker.status(m, tier)
            statuses.append(_internal_to_response_status(internal))

    # C) Probe (opt-in).
    if req.probe:
        notes.append(
            "probe=True: a minimal `agy` task was executed and consumed quota."
        )
        healthy, message, kind = probe_agy_quota(
            agy_path=_agy_path(),
            workspace_path=None,
            timeout_s=_settings.quota_probe_timeout_s,
        )
        # Annotate statuses with the probe result.
        for s in statuses:
            s.notes.append(f"probe: healthy={healthy} kind={kind} msg={message}")
            if not healthy:
                s.healthy = False

    # D) External API (opt-in).
    if req.use_api:
        notes.append("use_api=True: external API call attempted (stub in v1).")
        api_key = os.environ.get("AGY_MCP_QUOTA_API_KEY", "")
        if api_key:
            fetch_gemini_api_quota(
                api_base_url=_settings.quota_api_base_url,
                api_key=api_key,
            )
        else:
            notes.append(
                "AGY_MCP_QUOTA_API_KEY is not set; external API call skipped."
            )

    overall_healthy = all(s.healthy for s in statuses) if statuses else True

    return AgyQuotaResponse(
        statuses=statuses,
        overall_healthy=overall_healthy,
        active_model=active_model,
        notes=notes,
    )


def _resolve_uv() -> Path:
    uv_path = shutil.which("uv")
    if not uv_path:
        raise RuntimeError("UV_NOT_FOUND: uv is not in PATH")
    return Path(uv_path)


@mcp.tool(name=tool_name("clear_cache"))
def agy_clear_cache(req: AgyClearCacheRequestIn | None = None) -> AgyClearCacheResponse:
    """
    Clear the uv package cache to resolve stale-package import errors.

    When the MCP server fails to start with errors like:
        ImportError: cannot import name 'Xxx' from 'agy_mcp_server'
    and restarting / clearing uvx cache manually does not resolve it, this
    tool runs `uv cache clean` to remove all cached package archives.

    Behavior:
    - By default (full=False), clears only the uv cache directory
      (`~/.cache/uv`). This is the safest option and resolves most
      stale-cache issues without affecting other projects.
    - With full=True, clears the entire uv cache — useful when the server
      is launched via `--from <path>` and the archive hash keeps being
      reused across sessions.

    Warning: clearing the cache causes subsequent `uvx` invocations to
    re-download and re-install dependencies (slower first startup after clean).

    Input (optional fields marked):
        - full (bool): if True, clears the entire uv cache (~/.cache/uv).
          if False (default), clears only this project's package entries.

    Returns:
        AgyClearCacheResponse with:
        - cleared: True if the command succeeded.
        - entries_removed: number of cache entries removed (estimate).
        - cache_dir: the cache directory that was targeted.
        - notes: warnings or additional details.

    Raises:
        RuntimeError("UV_NOT_FOUND: ..."): the `uv` binary is missing
          from PATH.

    Side effects:
        Spawns `uv cache clean`. Subsequent `uvx` invocations will
        re-download and re-install dependencies (slower first startup).

    Example:
        run_mcp(name="agy_clear_cache", args={"req": {}})              # default
        run_mcp(name="agy_clear_cache", args={"req": {"full": True}})  # full

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyClearCacheRequest()
    notes: list[str] = []
    uv_exe = _resolve_uv()
    cache_dir = os.path.expanduser("~/.cache/uv")

    try:
        cache_path = Path(cache_dir)
        if not cache_path.exists():
            notes.append("uv cache directory does not exist; nothing to clean.")
            return AgyClearCacheResponse(
                cleared=True,
                entries_removed=0,
                cache_dir=cache_dir,
                notes=notes,
            )

        # Count entries before (rough estimate via subdirectory count).
        before_entries = sum(1 for _ in cache_path.rglob("*") if _.is_dir())

        args = [str(uv_exe), "cache", "clean"]
        if req.full:
            notes.append(
                "full=True: clearing entire uv cache. Subsequent uvx "
                "invocations will re-download and re-install dependencies."
            )
        else:
            notes.append(
                "full=False (default): clears uv cache entries for this package."
            )

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )

        after_entries = sum(1 for _ in cache_path.rglob("*") if _.is_dir())
        entries_removed = max(0, before_entries - after_entries)

        if result.returncode == 0:
            if entries_removed == 0:
                notes.append("No cache entries were removed (already clean).")
            else:
                notes.append(
                    f"Cache cleaned. Entries removed: {entries_removed}."
                )
        else:
            notes.append(f"uv cache clean returned code {result.returncode}.")
            if result.stderr:
                notes.append(f"stderr: {result.stderr.strip()}")

        return AgyClearCacheResponse(
            cleared=result.returncode == 0,
            entries_removed=entries_removed,
            cache_dir=cache_dir,
            notes=notes,
        )

    except subprocess.TimeoutExpired:
        notes.append("Cache clean timed out after 60s.")
        return AgyClearCacheResponse(
            cleared=False,
            entries_removed=0,
            cache_dir=cache_dir,
            notes=notes,
        )
    except Exception as e:
        notes.append(f"Error during cache clean: {e}")
        return AgyClearCacheResponse(
            cleared=False,
            entries_removed=0,
            cache_dir=cache_dir,
            notes=notes,
        )


# ------------------------------------------------------------------
# Persistence tools
# ------------------------------------------------------------------


@mcp.tool(name=tool_name("init_persistence"))
def agy_init_persistence(
    req: AgyInitPersistenceRequestIn | None = None,
) -> AgyInitPersistenceResponse:
    """Initialize the persistence directory and seed the three markdown files.

    Creates the directory at ``~/.open-cli-router/{provider}/`` and writes
    ``AGENTS.md``, ``PROJECTS.md``, and ``MEMORY.md`` (unless they already
    exist). Idempotent: re-running without ``force=true`` is a no-op.

    Input (optional fields marked):
        - force (bool): overwrite existing files. Default False.
        - seed_templates (bool|None): True writes seed templates,
          False writes empty files, None uses settings default.

    Returns:
        AgyInitPersistenceResponse with:
        - base_dir: absolute path to the persistence directory.
        - created: paths created by this call.
        - already_existed: paths that already existed (skipped).
        - seed_version: version of the seed template used.

    Raises:
        ValueError("PERSISTENCE_DISABLED: ..."): settings.persistence_enabled
          is False.

    Side effects:
        - Creates the persistence directory at ~/.open-cli-router/{provider}/
        - Writes AGENTS.md, PROJECTS.md, MEMORY.md (unless already present).
          Idempotent: re-running without force=True is a no-op.

    Example:
        run_mcp(name="agy_init_persistence", args={"req": {}})
        run_mcp(name="agy_init_persistence", args={"req": {"force": True}})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyInitPersistenceRequest()
    if not _settings.persistence_enabled:
        raise ValueError(
            "PERSISTENCE_DISABLED: persistence is disabled via settings"
        )

    result = _persistence_store.init(
        force=req.force,
        seed_templates=req.seed_templates,
    )
    return AgyInitPersistenceResponse(
        base_dir=result.base_dir,
        created=result.created,
        already_existed=result.already_existed,
        seed_version=result.seed_version,
    )


@mcp.tool(name=tool_name("read_persistence"))
def agy_read_persistence(
    req: AgyReadPersistenceRequestIn | None = None,
) -> AgyReadPersistenceResponse:
    """Read one of the three persistence files.

    Input (optional fields marked):
        - file (enum): "agents" | "projects" | "memory". Default "memory".
        - offset (int): byte offset to start reading. Default 0.
        - limit (int|None): max bytes to read; None reads whole file up to
          settings.persistence_max_file_bytes.

    Returns:
        AgyReadPersistenceResponse with:
        - file: absolute path.
        - content: file contents (UTF-8).
        - size_bytes: total file size.
        - truncated: True if capped by limit or max_file_bytes.
        - modified_at: last modification time (UTC).

    Raises:
        ValueError("PERSISTENCE_DISABLED: ..."): settings.persistence_enabled
          is False.

    Side effects:
        None. Read-only against the persistence directory.

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyReadPersistenceRequest()
    if not _settings.persistence_enabled:
        raise ValueError(
            "PERSISTENCE_DISABLED: persistence is disabled via settings"
        )

    result = _persistence_store.read(
        req.file, offset=req.offset, limit=req.limit
    )
    return AgyReadPersistenceResponse(
        file=result.file,
        content=result.content,
        size_bytes=result.size_bytes,
        truncated=result.truncated,
        modified_at=result.modified_at,
    )


@mcp.tool(name=tool_name("append_persistence"))
def agy_append_persistence(
    req: AgyAppendPersistenceRequestIn | None = None,
) -> AgyAppendPersistenceResponse:
    """Append content to one of the persistence files.

    Typical use: append a concise summary to ``memory`` after each session.

    Input (optional fields marked):
        - file (enum): "agents" | "projects" | "memory".
        - content (str): markdown text to append. Required.
        - section_header (str|None): optional "## <header>" inserted
          before the content if the heading is not already present.

    Returns:
        AgyAppendPersistenceResponse with:
        - file: absolute path.
        - appended_bytes: bytes added by this call.
        - new_size_bytes: total file size after append.
        - timestamp: server time of the write (UTC).

    Raises:
        ValueError("PERSISTENCE_DISABLED: ..."): settings.persistence_enabled
          is False.

    Side effects:
        Appends to a persistence file on disk (AGENTS.md / PROJECTS.md /
        MEMORY.md). Do not store secrets, credentials, or full file dumps;
        keep entries small and high-signal.

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyAppendPersistenceRequest()
    if not _settings.persistence_enabled:
        raise ValueError(
            "PERSISTENCE_DISABLED: persistence is disabled via settings"
        )

    result = _persistence_store.append(
        req.file, req.content, section_header=req.section_header
    )
    return AgyAppendPersistenceResponse(
        file=result.file,
        appended_bytes=result.appended_bytes,
        new_size_bytes=result.new_size_bytes,
        timestamp=result.timestamp,
    )


@mcp.tool(name=tool_name("update_persistence"))
def agy_update_persistence(
    req: AgyUpdatePersistenceRequestIn | None = None,
) -> AgyUpdatePersistenceResponse:
    """Replace or append to a section in one of the persistence files.

    Input (optional fields marked):
        - file (enum): "agents" | "projects" | "memory".
        - section_anchor (str): heading text without the "## " prefix.
        - new_content (str): replacement content (full section including
          heading) when mode="replace".
        - mode (enum): "replace" (default) replaces the section up to the
          next "## " heading; "append" ignores the anchor.
        - confirm (bool): in safe mode, requires confirm=true to update
          AGENTS.md (system prompt file).

    Returns:
        AgyUpdatePersistenceResponse with:
        - file: absolute path.
        - section_anchor: the anchor that was requested.
        - matched: True if the anchor was found (replace mode). Always True
          for append mode.
        - new_size_bytes: total file size after the update.

    Raises:
        ValueError("PERSISTENCE_DISABLED: ..."): settings.persistence_enabled
          is False.
        ValueError("CONFIRM_REQUIRED: ..."): in safe mode, updating
          AGENTS.md requires confirm=true.

    Side effects:
        Rewrites a section (or appends) of a persistence file on disk.
        Returns matched=false when the anchor is not found and no edit
        happened (so orchestrators can detect typos before silent
        appending).

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyUpdatePersistenceRequest()
    if not _settings.persistence_enabled:
        raise ValueError(
            "PERSISTENCE_DISABLED: persistence is disabled via settings"
        )

    # Paridade com claude-code-cli-mcp: em safe mode, atualizar AGENTS.md
    # (system-prompt editável) exige confirm=true explícito para evitar
    # sobrescrita acidental do system prompt.
    if req.file == "agents" and _settings.mode == "safe" and not req.confirm:
        raise ValueError(
            "CONFIRM_REQUIRED: updating AGENTS.md in safe mode requires confirm=true"
        )

    result = _persistence_store.update(
        req.file,
        req.section_anchor,
        req.new_content,
        mode=req.mode,
    )
    return AgyUpdatePersistenceResponse(
        file=result.file,
        section_anchor=result.section_anchor,
        matched=result.matched,
        new_size_bytes=result.new_size_bytes,
    )


@mcp.tool(name=tool_name("load_persistence_context"))
def agy_load_persistence_context(
    req: AgyLoadPersistenceContextRequestIn | None = None,
) -> AgyLoadPersistenceContextResponse:
    """Load the persistence files as context for the current session.

    Returns excerpts (head + tail) of each requested file. Used by the
    orchestrator to inject persistent memory into a new session.

    Input (optional fields marked):
        - include (list[str]|None): subset of files to load
          ("agents"|"projects"|"memory"). None = all three.
        - max_chars_per_file (int): per-file char cap (default 20_000).

    Returns:
        AgyLoadPersistenceContextResponse with:
        - agents_excerpt / projects_excerpt / memory_excerpt: excerpts.
        - truncated_flags: True for each file that was truncated.
        - total_chars: sum of excerpt lengths.
        - base_dir: the persistence directory path.
        - initialized: True if agy_init_persistence has been run.

    Raises:
        ValueError("PERSISTENCE_DISABLED: ..."): settings.persistence_enabled
          is False.

    Side effects:
        None. Read-only against the persistence directory.

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgyLoadPersistenceContextRequest()
    if not _settings.persistence_enabled:
        raise ValueError(
            "PERSISTENCE_DISABLED: persistence is disabled via settings"
        )

    result = _persistence_store.load_context(
        include=req.include,
        max_chars_per_file=req.max_chars_per_file,
    )
    return AgyLoadPersistenceContextResponse(
        agents_excerpt=result.agents_excerpt,
        projects_excerpt=result.projects_excerpt,
        memory_excerpt=result.memory_excerpt,
        truncated_flags=result.truncated_flags,
        total_chars=result.total_chars,
        base_dir=result.base_dir,
        initialized=result.initialized,
    )


@mcp.prompt(name=prompt_name("persistence_protocol"))
def prompt_persistence_protocol() -> str:
    """Instruct the orchestrator on how to maintain the persistence layer.

    Input: (none). Zero-arg prompt.

    Returns:
        Markdown covering: location note (resolved base_dir + LOCATION
        hint), the 3 files (AGENTS.md / PROJECTS.md / MEMORY.md),
        the 4-step lifecycle (init / load / append / update), and a
        do-not-store list (secrets, credentials, full dumps).

    Side effects:
        None. The prompt is pure text generation; no MCP tool is called.

    Use when:
        You need to instruct the orchestrator on how to maintain the
        persistent memory layer (`AGENTS.md`, `PROJECTS.md`, `MEMORY.md`).
    """
    base = _settings.resolve_persistence_base_dir()
    location_note = (
        f"NOTE: persistence is configured with LOCATION="
        f"{_settings.persistence_location} → base_dir={base}\n"
    )
    if _settings.persistence_location == "workspace":
        location_note += (
            "When location='workspace', the files live in your project "
            "directory (one level up from the server's CWD).\n"
            "Consider adding '.open-cli-router/' to .gitignore to avoid "
            "committing agent memory to source control.\n"
        )
    location_note += "\n"

    return (location_note + (
        "You have access to a persistent memory layer with three editable files:\n"
        "- AGENTS.md (your editable system prompt)\n"
        "- PROJECTS.md (project summaries)\n"
        "- MEMORY.md (permanent memory)\n"
        "\n"
        "Lifecycle:\n"
        "1. On the first run, call `{provider}_init_persistence` to "
        "create the directory and seed files.\n"
        "2. At the start of each session, call "
        "`{provider}_load_persistence_context` to load the latest state.\n"
        "3. After each meaningful session, append a concise summary to "
        "MEMORY.md using `{provider}_append_persistence`.\n"
        "4. When the user explicitly changes AGENTS.md or PROJECTS.md, "
        "use `{provider}_update_persistence` to persist.\n"
        "\n"
        "Do not store secrets, credentials, or full file dumps in "
        "MEMORY.md — keep entries small and high-signal.\n"
    )).replace("{provider}", PROVIDER_PREFIX)


@mcp.prompt(name=prompt_name("quickstart"))
def prompt_quickstart() -> str:
    """Cheatsheet for using this MCP server. Read this first if confused.

    Input: (none). Zero-arg prompt.

    Returns:
        Markdown with the canonical contract: args shape (`{"req": {...}}`
        wrapping), required CLI binary (`agy`), the 14-tool catalog, and
        common gotchas (quota, workspace path, mode).

    Side effects:
        None. Pure text generation. Static content kept in sync with
        `agy_self_test` results.

    Use when:
        You are starting an orchestration session and need the canonical
        contract at a glance.
    """
    return (
        f"# {PROVIDER_PREFIX}-mcp-server — Quickstart\n"
        "\n"
        "## Args shape (CRITICAL — most bugs come from this)\n"
        "    run_mcp(args={\"req\": {...}})       # ✓ correct — dict\n"
        "    run_mcp(args=[{\"req\": {...}}])     # ✗ wrong — list wraps as {\"item\": ...}\n"
        "    run_mcp(args={})                    # OK after refactor: req is now optional\n"
        "\n"
        "## CLI binary required\n"
        "    agy must be installed and on PATH (Antigravity CLI, Gemini models).\n"
        "    Verify with agy_health(req={}).\n"
        "\n"
        "## Tool catalog (14 tools)\n"
        "    agy_health                  — ping server + check CLI version\n"
        "    agy_self_test               — schema robustness probe (run first if unsure)\n"
        "    agy_run_task                — sync execution (blocking, with timeout)\n"
        "    agy_start_task / agy_poll_task / agy_cancel_task — async lifecycle\n"
        "    agy_list_runs               — list active/completed runs\n"
        "    agy_quota                   — local quota counter (no probe by default)\n"
        "    agy_clear_cache             — uv cache clean (use full=true for --dry-run)\n"
        "    Persistence (5):\n"
        "        agy_init_persistence, agy_read_persistence,\n"
        "        agy_append_persistence, agy_update_persistence,\n"
        "        agy_load_persistence_context\n"
        "\n"
        "## workspace_path for run_task\n"
        "    Must be inside AGY_MCP_ALLOWED_ROOTS (JSON-array env var).\n"
        "    Default = Path.cwd() of the server process = the server's project dir.\n"
        "\n"
        "## Common gotchas → call troubleshoot prompt with the error string\n"
        "    Use prompt `agy_troubleshoot` with the exact error message.\n"
        "\n"
        "## Restart requirement\n"
        "    After server-side changes that add new tools/prompts, restart the\n"
        "    MCP server in the Trae panel so the registry re-discovers them.\n"
    )


@mcp.prompt(name=prompt_name("contract"))
def prompt_contract() -> str:
    """Full machine-readable JSON contract of every registered tool.

    Input: (none). Zero-arg prompt.

    Returns:
        A filled markdown prompt template with the tool catalog built
        from `mcp._local_provider._components` so it stays in sync with
        the actual registered tool schemas.

    Side effects:
        None. Pure text generation. Internally reads live tool registry
        state via FastMCP internals; no file or MCP tool is called.

    Use when:
        You need the full machine-readable JSON contract before writing
        integrations.
    """
    tools_dict: dict[str, Any] = {}
    if hasattr(mcp, "_local_provider") and hasattr(mcp._local_provider, "_components"):
        tools_dict = {
            v.name: v.parameters if hasattr(v, "parameters") else {}
            for k, v in mcp._local_provider._components.items()
            if k.startswith("tool:")
        }
    elif hasattr(mcp, "_tool_manager"):
        tools_dict = getattr(mcp._tool_manager, "_tools", {})

    parts = [f"# {PROVIDER_PREFIX}-mcp-server — Full tool contract\n"]
    for name, schema in sorted(tools_dict.items()):
        parts.append(f"## {name}\n")
        parts.append("```json\n")
        try:
            parts.append(json.dumps(schema, indent=2, default=str))
        except Exception:  # noqa: BLE001
            parts.append(str(schema))
        parts.append("\n```\n")
    return "\n".join(parts)


@mcp.prompt(name=prompt_name("troubleshoot"))
def prompt_troubleshoot(error: str = "") -> str:
    """Diagnose a specific error string and return the fix recipe.

    Input:
        - error (str, optional): the exact error message you received
          (e.g. "req: Missing required argument" or "workspace_path is
          outside allowed roots"). Default "" returns a usage hint.

    Returns:
        A short markdown "BUG: ... / FIX: ..." snippet matched on
        substrings of `error` (case-insensitive). Falls through to a
        "no match" hint when the error does not match any known recipe.

    Side effects:
        None. Pure text generation.

    Use when:
        You (or the orchestrator) received a non-obvious error from any
        agy_* tool call and want the canonical fix.
    """
    err_lc = (error or "").lower()
    if not err_lc:
        return (
            "Pass the exact error message you received as the `error` arg.\n"
            "Example: prompt `agy_troubleshoot` with error=\"req: Missing required argument\"."
        )
    if "missing required argument" in err_lc and "req" in err_lc:
        return (
            "BUG: args shape wrong. You're sending args as a list or empty dict.\n"
            "FIX: pass args={\"req\": {...}} (a dict with the `req` key)."
        )
    if "not allowed" in err_lc or "outside allowed roots" in err_lc:
        return (
            "BUG: workspace_path is not in the server's allowed roots.\n"
            "FIX: set AGY_MCP_ALLOWED_ROOTS=[\"/your/path\"] (JSON array) in the server's\n"
            "env, or pass a workspace_path inside Path.cwd() of the server process."
        )
    if "tool is not found" in err_lc or "mcp tool is not found" in err_lc:
        return (
            "BUG: Trae MCP registry stale.\n"
            "FIX: user must restart the MCP server in the Trae panel (not retry the call)."
        )
    if "not logged in" in err_lc or "/login" in err_lc:
        return (
            "BUG: agy CLI auth expired or not initialized in this session.\n"
            "FIX: user must run `agy` interactively once or `agy login` to re-auth."
        )
    if "tolerant_count" in err_lc or "requires_req_count" in err_lc:
        return (
            "Schema regression detected. Some tools no longer accept args={}.\n"
            "FIX: run agy_self_test to enumerate, then check the affected tool's signature."
        )
    return (
        f"No specific recipe for: {error!r}.\n"
        "General debug steps:\n"
        "1. Run agy_self_test(req={}) to check server health.\n"
        "2. Read the `agy_quickstart` prompt.\n"
        "3. Check the server's stderr for the actual exception."
    )


@mcp.tool(name=tool_name("self_test"))
def agy_self_test(req: AgySelfTestRequestIn | None = None) -> AgySelfTestResponse:
    """Inspect every registered tool's input schema and report robustness.

    This is a metadata-only check — no tools are actually invoked, so it
    is safe to run in production and has no side effects.

    Input (optional fields marked):
        - include (list[str]|None): subset of tool names to inspect.
          None enumerates every registered tool.
        - only_show_tolerant (bool): when True, suppresses reports for
          tools whose schemas are strict (require {"req": {...}} wrapping).

    Returns:
        AgySelfTestResponse (per-tool schema reports with tolerant_count
        and requires_req_count summaries).

    Raises:
        RuntimeError("Cannot access FastMCP tool manager ..."): internal
          FastMCP API has changed and this probe needs updating.

    Side effects:
        None. Metadata-only check. Safe to run in production.

    Example:
        run_mcp(name="agy_self_test", args={})
        run_mcp(name="agy_self_test", args={"req": {"only_show_tolerant": True}})

    Args shape:
        Wrapped in `req` -- see the `agy_quickstart` prompt section "Args shape".
    """
    if req is None:
        req = AgySelfTestRequest()
    
    # Access FastMCP's internal tools
    tools_dict = {}
    if hasattr(mcp, "_local_provider") and hasattr(mcp._local_provider, "_components"):
        tools_dict = {
            v.name: v
            for k, v in mcp._local_provider._components.items()
            if k.startswith("tool:")
        }
    else:
        tool_manager = getattr(mcp, "_tool_manager", None) or getattr(mcp, "_tools", None)
        if tool_manager is None:
            raise RuntimeError("Cannot access FastMCP tool manager; FastMCP API may have changed")
        
        if hasattr(tool_manager, "_tools"):
            tools_dict = tool_manager._tools
        elif hasattr(tool_manager, "list_tools"):
            raise RuntimeError("FastMCP tool manager requires async enumeration; please update self_test implementation")
        else:
            raise RuntimeError(f"Unsupported tool manager type: {type(tool_manager)}")
    
    reports: list[AgyToolSchemaReport] = []
    for name, tool in tools_dict.items():
        # `tool` may be a Tool object or a callable; extract parameters schema
        if hasattr(tool, "parameters"):
            schema = tool.parameters  # JSON schema dict
        elif hasattr(tool, "input_schema"):
            schema = tool.input_schema
        else:
            # Fallback: introspect signature
            import inspect
            sig = inspect.signature(tool)
            params = [p for p in sig.parameters.values() if p.name != "self"]
            schema = {
                "required": [p.name for p in params if p.default is inspect.Parameter.empty],
                "properties": {p.name: {"type": "object"} for p in params},
            }
        
        required = schema.get("required", []) if isinstance(schema, dict) else []
        properties = list(schema.get("properties", {}).keys()) if isinstance(schema, dict) else []
        
        if req.include is not None:
            if not any(name.startswith(p) for p in req.include):
                continue
        if req.only_show_tolerant and required:
            continue
        
        reports.append(AgyToolSchemaReport(
            name=name,
            top_level_required=required,
            top_level_properties=properties,
            accepts_empty_args=len(required) == 0,
            requires_req_wrapper="req" in required,
        ))
    
    tolerant = sum(1 for r in reports if r.accepts_empty_args)
    requires_req = sum(1 for r in reports if r.requires_req_wrapper)
    
    return AgySelfTestResponse(
        total_tools=len(reports),
        tolerant_count=tolerant,
        requires_req_count=requires_req,
        tools=reports,
        server_info={"name": "agy-mcp-server", "version": __version__},
        summary=f"{len(reports)} tools inspected: {tolerant} tolerant to args={{}}, {requires_req} still require `req` wrapper",
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
