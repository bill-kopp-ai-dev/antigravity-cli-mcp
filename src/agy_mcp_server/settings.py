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
    # "global"  → use ``persistence_base_dir`` (e.g., ~/.open-cli-router).
    # "workspace" → use ``<cwd_parent>/.open-cli-router`` where ``cwd_parent``
    #               is the parent of the server's CWD (= the user's workspace).
    persistence_location: Literal["global", "workspace"] = "global"
    persistence_base_dir: Path = Field(
        default_factory=lambda: Path("~/.open-cli-router").expanduser()
    )
    persistence_max_file_bytes: int = 524_288  # 512 KiB
    persistence_backup_on_write: bool = False
    persistence_backup_keep: int = 10  # Phase 3: rotate, keep last N
    persistence_seed_templates: bool = True
    persistence_truncation_head_ratio: float = 0.2  # Phase 3: 20% head, 80% tail

    def resolved_allowed_roots(self) -> list[Path]:
        if self.allowed_roots:
            return [p.expanduser().resolve() for p in self.allowed_roots]
        return [Path.cwd().resolve()]

    def resolve_persistence_base_dir(self) -> Path:
        """Resolve the persistence base directory based on ``persistence_location``.

        Resolution order:

        1. If ``persistence_base_dir`` starts with the special token
           ``$cwd_parent``, expand it relative to the parent of the server's
           CWD. This is the escape hatch for custom paths (e.g.
           ``$cwd_parent/.my-persistence``).
        2. Otherwise, if ``persistence_location == "workspace"``, return
           ``<cwd_parent>/.open-cli-router``.
        3. Otherwise (default), return the (expanded) ``persistence_base_dir``.

        Returns:
            The absolute, expanded path (NOT created by this function).
        """
        raw = self.persistence_base_dir
        # Escape hatch: $cwd_parent literal in the value itself.
        raw_str = str(raw)
        if raw_str.startswith("$cwd_parent"):
            suffix = raw_str[len("$cwd_parent"):]
            # Strip leading slashes so "tmp_path / '/foo'" is interpreted
            # as a relative child, not an absolute path override.
            suffix = suffix.lstrip("/")
            return (Path.cwd().parent / suffix).expanduser()

        if self.persistence_location == "workspace":
            return Path.cwd().parent / ".open-cli-router"

        return raw.expanduser()
