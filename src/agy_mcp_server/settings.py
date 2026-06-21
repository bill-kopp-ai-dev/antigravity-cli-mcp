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

    def resolved_allowed_roots(self) -> list[Path]:
        if self.allowed_roots:
            return [p.expanduser().resolve() for p in self.allowed_roots]
        return [Path.cwd().resolve()]
