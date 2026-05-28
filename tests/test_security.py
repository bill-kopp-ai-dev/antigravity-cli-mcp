import importlib
import subprocess
import time


def _load_server(monkeypatch, *, mode: str, allowed_roots: str):
    monkeypatch.setenv("AGY_MCP_FIX_ANTIGRAVITY_MCP_CONFIG", "false")
    monkeypatch.setenv("AGY_MCP_MODE", mode)
    monkeypatch.setenv("AGY_MCP_ALLOWED_ROOTS", allowed_roots)
    import agy_mcp_server.server as server

    return importlib.reload(server)


def test_safe_mode_blocks_env_and_extra_args(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, mode="safe", allowed_roots=f'[\"{tmp_path}\"]')
    workspace = tmp_path

    from agy_mcp_server.models import AgyRunTaskRequest

    req = AgyRunTaskRequest(
        workspace_path=str(workspace),
        prompt="OK",
        capture_changes=False,
    )
    req.options.env = {"X": "1"}
    try:
        server.agy_run_task(req)
        assert False
    except ValueError as e:
        assert "NOT_ALLOWED" in str(e)

    req = AgyRunTaskRequest(
        workspace_path=str(workspace),
        prompt="OK",
        capture_changes=False,
    )
    req.options.extra_args = ["--log-file", "/tmp/x"]
    try:
        server.agy_run_task(req)
        assert False
    except ValueError as e:
        assert "NOT_ALLOWED" in str(e)


def test_permissive_mode_enforces_allowlists(monkeypatch, tmp_path):
    monkeypatch.setenv("AGY_MCP_ALLOW_ENV_KEYS", '["FOO"]')
    monkeypatch.setenv("AGY_MCP_ALLOW_EXTRA_ARGS", '["--sandbox"]')
    server = _load_server(monkeypatch, mode="permissive", allowed_roots=f'[\"{tmp_path}\"]')

    from agy_mcp_server.models import AgyRunTaskRequest

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="OK",
        capture_changes=False,
    )
    req.options.env = {"BAR": "1"}
    try:
        server.agy_run_task(req)
        assert False
    except ValueError as e:
        assert "NOT_ALLOWED" in str(e)

    req = AgyRunTaskRequest(
        workspace_path=str(tmp_path),
        prompt="OK",
        capture_changes=False,
    )
    req.options.extra_args = ["--unknown-flag"]
    try:
        server.agy_run_task(req)
        assert False
    except ValueError as e:
        assert "NOT_ALLOWED" in str(e)


def test_async_start_poll_cancel_flow(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, mode="safe", allowed_roots=f'[\"{tmp_path}\"]')

    def fake_build(workspace, request):
        proc = subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        return proc, None, f"{request.prompt}\n"

    monkeypatch.setattr(server, "_build_agy_popen", fake_build)

    from agy_mcp_server.models import AgyCancelTaskRequest, AgyPollTaskRequest, AgyStartTaskRequest

    started = server.agy_start_task(
        AgyStartTaskRequest(workspace_path=str(tmp_path), prompt="OK", capture_changes=False)
    )
    run_id = started.run_id

    pol = server.agy_poll_task(AgyPollTaskRequest(run_id=run_id))
    assert pol.status == "running"

    canceled = server.agy_cancel_task(AgyCancelTaskRequest(run_id=run_id, force=False))
    assert canceled.canceled is True

    deadline = time.time() + 5
    while time.time() < deadline:
        pol = server.agy_poll_task(AgyPollTaskRequest(run_id=run_id))
        if pol.status != "running":
            assert pol.result is not None
            return
        time.sleep(0.1)

    assert False
