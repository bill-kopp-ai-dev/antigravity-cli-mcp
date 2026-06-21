from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGY_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    allowed_roots: list[Path] = Field(default_factory=list)
    mode: Literal["safe", "permissive"] = "safe"
    default_timeout_s: int = 300
    max_output_bytes: int = 2_000_000
    max_runs: int = 50
    agy_path: str = "agy"
    snapshot_max_file_bytes: int = 512_000
    ignore_dir_names: set[str] = Field(
        default_factory=lambda: {".git", ".antigravitycli", ".venv", "__pycache__"}
    )
    allow_env_keys: set[str] = Field(default_factory=set)
    allow_extra_args: set[str] = Field(default_factory=set)
    force_sandbox_in_safe_mode: bool = True

    fix_antigravity_mcp_config: bool = False
    antigravity_mcp_config_path: Path = Field(
        default_factory=lambda: Path("~/.gemini/config/mcp_config.json").expanduser()
    )

    # ---- Quota tracking ----
    quota_active_model: str = "unknown"
    quota_tier: Literal["free", "pro", "ultra", "enterprise", "unknown"] = "unknown"
    quota_period_hours: float = 5.0
    quota_tier_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "free": 30,
            "pro": 200,
            "ultra": 1000,
            "enterprise": 5000,
        }
    )
    quota_probe_timeout_s: int = 30
    quota_api_base_url: str = "https://generativelanguage.googleapis.com"

    # ---- Persistence ----
    persistence_enabled: bool = True
    persistence_base_dir: Path = Field(
        default_factory=lambda: Path("~/.open-cli-router").expanduser()
    )
    persistence_max_file_bytes: int = 524_288  # 512 KiB
    persistence_backup_on_write: bool = False
    persistence_seed_templates: bool = True

    def resolved_allowed_roots(self) -> list[Path]:
        if self.allowed_roots:
            return [p.expanduser().resolve() for p in self.allowed_roots]
        return [Path.cwd().resolve()]
