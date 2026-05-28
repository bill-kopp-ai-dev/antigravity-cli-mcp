from __future__ import annotations

import signal
import threading
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastmcp import FastMCP

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
    AgyHealthRequest,
    AgyHealthResponse,
    AgyListRunsRequest,
    AgyListRunsResponse,
    AgyPollTaskRequest,
    AgyPollTaskResponse,
    AgyRunTaskRequest,
    AgyRunTaskResponse,
    AgyStartTaskRequest,
    AgyStartTaskResponse,
    AgyRunResult,
    AgyRunSummary,
    WorkspaceChanges,
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

if _settings.fix_antigravity_mcp_config:
    ensure_valid_mcp_config_json(_settings.antigravity_mcp_config_path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_workspace_path(workspace_path: str) -> Path:
    p = Path(workspace_path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("INVALID_WORKSPACE: workspace_path must be an existing directory")

    allowed_roots = _settings.resolved_allowed_roots()
    if not any(str(p).startswith(str(root) + "/") or p == root for root in allowed_roots):
        raise ValueError("NOT_ALLOWED: workspace_path is outside allowed roots")

    return p


def _agy_path() -> str:
    resolved = shutil.which(_settings.agy_path)
    if resolved is None:
        raise RuntimeError("AGY_NOT_FOUND: agy not found in PATH")
    return resolved


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
    """
    Orchestration playbook for running a single synchronous `agy_run_task` safely.

    Use this prompt when you (the orchestrator agent) need a reliable, repeatable sequence
    to run one Antigravity CLI task and optionally capture workspace changes.
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
    """
    Orchestration playbook for running `agy_start_task` + `agy_poll_task` + `agy_cancel_task`.

    Use this prompt when you need non-blocking execution, progress polling, and a safe
    cancellation strategy.
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
    """
    Guidance for model selection when using this MCP server.

    Use this prompt to explain the model-selection limitation and how a user configures
    the model directly in the Antigravity CLI via `/model`.
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
    """
    Safety rules and workspace constraints for orchestrators.

    Use this prompt to remind an orchestrator how to stay within server safety boundaries
    and how to choose safe defaults.
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


@mcp.tool
def agy_health(req: AgyHealthRequest) -> AgyHealthResponse:
    """
    Health check for the Antigravity CLI binary (`agy`).

    This tool verifies that `agy` is discoverable on PATH and returns its version string.

    Input:
    - expected_version (optional): if provided, the response sets ok=false when the installed
      version does not exactly match.

    Output:
    - agy_path: resolved absolute path to the `agy` binary
    - agy_version: raw `agy --version` output (trimmed)
    - ok: whether the health check passed (and version matched, if requested)
    - notes: details such as version mismatch information
    """
    agy = _agy_path()
    version = subprocess.check_output([agy, "--version"], text=True).strip()

    notes: list[str] = []
    ok = True
    if req.expected_version and version != req.expected_version:
        ok = False
        notes.append(f"expected_version_mismatch: expected={req.expected_version} got={version}")

    return AgyHealthResponse(agy_path=agy, agy_version=version, ok=ok, notes=notes)


@mcp.tool
def agy_run_task(req: AgyRunTaskRequest) -> AgyRunTaskResponse:
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
    - This MCP server does not control the reasoning model used by `agy`. `agy` uses the model
      configured in the CLI itself (via the interactive /model command) and persists it across
      sessions.
    """
    workspace = _resolve_workspace_path(req.workspace_path)
    _validate_exec_options(req)

    before_snapshot = None
    if req.capture_changes and not is_git_repo(workspace) and req.change_scope == "workspace":
        before_snapshot = snapshot_tree(
            workspace,
            ignore_dir_names=_settings.ignore_dir_names,
            max_file_bytes=_settings.snapshot_max_file_bytes,
        )

    started_at = _now()
    stdout, stderr, exit_code, timed_out = _run_agy(workspace, req)
    finished_at = _now()

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

    return AgyRunTaskResponse(result=result, changes=changes)


@mcp.tool
def agy_start_task(req: AgyStartTaskRequest) -> AgyStartTaskResponse:
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
    - This MCP server does not control the reasoning model used by `agy`. Configure it inside
      the CLI via /model.
    """
    workspace = _resolve_workspace_path(req.workspace_path)
    _validate_exec_options(req)

    before_snapshot = None
    if req.capture_changes and not is_git_repo(workspace) and req.change_scope == "workspace":
        before_snapshot = snapshot_tree(
            workspace,
            ignore_dir_names=_settings.ignore_dir_names,
            max_file_bytes=_settings.snapshot_max_file_bytes,
        )

    started_at = _now()
    run_id = f"run-{uuid4()}"

    proc, _, input_text = _build_agy_popen(workspace, req)
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

    return AgyStartTaskResponse(run_id=run_id, started_at=started_at)


@mcp.tool
def agy_poll_task(req: AgyPollTaskRequest) -> AgyPollTaskResponse:
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

    Errors:
    - Raises RUN_NOT_FOUND if run_id is unknown to both the active run set and the run store.
    """
    with _active_runs_lock:
        active = _active_runs.get(req.run_id)

    if active is not None:
        max_tail = min(_settings.max_output_bytes, 200_000)
        return AgyPollTaskResponse(
            status="running",
            result=None,
            partial_stdout=active.stdout_buf.tail(max_tail),
            partial_stderr=active.stderr_buf.tail(max_tail),
            changes=None,
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
    )


@mcp.tool
def agy_cancel_task(req: AgyCancelTaskRequest) -> AgyCancelTaskResponse:
    """
    Cancel an asynchronous task started by agy_start_task.

    Cancellation strategy:
    - force=false: send SIGTERM to the process group (graceful), falling back to terminate().
    - force=true: send SIGKILL to the process group (hard kill), falling back to kill().

    Output:
    - canceled=true when a running process was targeted for termination
    - status="canceled" | "already_done" | "not_found"

    Note:
    - Cancellation is best-effort. The process may still take a short time to exit; use
      agy_poll_task to observe the final status and retrieve output.
    """
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


@mcp.tool
def agy_list_runs(req: AgyListRunsRequest) -> AgyListRunsResponse:
    """
    List recent runs (both currently running and recently completed).

    Ordering:
    - Running tasks are listed first, newest to oldest.
    - Completed tasks are then listed from the in-memory run store, newest to oldest.

    Input:
    - limit: maximum number of entries to return

    Output:
    - runs: summaries containing run_id, workspace_path, status, and started_at
    """
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
