import subprocess

from agy_mcp_server.changes import git_changed_files, is_git_repo
from agy_mcp_server.hardening import ensure_valid_mcp_config_json
from agy_mcp_server.settings import Settings


def test_settings_load_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGY_MCP_MODE", raising=False)
    (tmp_path / ".env").write_text("AGY_MCP_MODE=permissive\n", encoding="utf-8")

    settings = Settings()

    assert settings.mode == "permissive"


def test_invalid_mcp_config_is_not_overwritten(tmp_path):
    config_path = tmp_path / "mcp_config.json"
    original = '{ invalid json'
    config_path.write_text(original, encoding="utf-8")

    changed = ensure_valid_mcp_config_json(config_path)

    assert changed is False
    assert config_path.read_text(encoding="utf-8") == original


def test_git_repo_detection_works_from_subdirectories(tmp_path):
    repo_root = tmp_path / "repo"
    nested_workspace = repo_root / "nested"
    nested_workspace.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo_root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    changed_file = nested_workspace / "new_file.py"
    changed_file.write_text("print('ok')\n", encoding="utf-8")

    assert is_git_repo(nested_workspace) is True
    assert git_changed_files(nested_workspace) == ["nested/new_file.py"]
