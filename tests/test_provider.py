"""Tests for the provider prefix standardization module."""

from __future__ import annotations

import pytest

from agy_mcp_server.provider import (
    PROVIDER_PREFIX,
    prompt_name,
    tool_name,
)


def test_provider_prefix_is_agy():
    """The provider prefix must be the Antigravity CLI short name."""
    assert PROVIDER_PREFIX == "agy"


def test_tool_name_basic():
    """Tool names follow the pattern `{prefix}_{suffix}`."""
    assert tool_name("health") == "agy_health"
    assert tool_name("run_task") == "agy_run_task"
    assert tool_name("clear_cache") == "agy_clear_cache"


def test_tool_name_uppercase_suffix_normalized():
    """Suffixes are lowercased to keep tool names stable across callers."""
    assert tool_name("HEALTH") == "agy_health"
    assert tool_name("Run_Task") == "agy_run_task"


def test_tool_name_strips_whitespace():
    """Leading/trailing whitespace is removed before building the name."""
    assert tool_name("  health  ") == "agy_health"


def test_tool_name_empty_suffix_raises():
    """An empty suffix must fail loudly, never produce a bare prefix."""
    with pytest.raises(ValueError):
        tool_name("")


def test_tool_name_invalid_chars_raise():
    """Suffixes with non-snake_case characters must fail."""
    for bad in ["foo bar", "foo-bar", "foo.bar", "foo/bar", "ação"]:
        with pytest.raises(ValueError):
            tool_name(bad)


def test_prompt_name_matches_tool_name():
    """Prompt names follow the same convention as tool names."""
    assert prompt_name("sync_orchestration") == tool_name("sync_orchestration")


def test_all_registered_tools_use_standardized_naming():
    """All tools registered on the MCP server follow the `{prefix}_*` naming."""
    import asyncio

    from agy_mcp_server.server import mcp

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    assert len(tools) >= 8, f"Expected at least 8 tools, found {len(tools)}"
    for t in tools:
        assert t.name.startswith(f"{PROVIDER_PREFIX}_"), (
            f"Tool {t.name!r} does not follow the {PROVIDER_PREFIX}_* naming"
        )


def test_forking_provider_prefix_renames_all_tools(monkeypatch):
    """Simulate a fork: changing PROVIDER_PREFIX renames every tool."""
    import agy_mcp_server.provider as provider_mod

    monkeypatch.setattr(provider_mod, "PROVIDER_PREFIX", "claude")

    assert tool_name("health") == "claude_health"
    assert tool_name("run_task") == "claude_run_task"
    assert tool_name("clear_cache") == "claude_clear_cache"


def test_all_tools_tolerate_empty_args():
    """Every tool must accept args={} (returning success or ValidationError, never TypeError)."""
    from pydantic import ValidationError
    from agy_mcp_server.server import mcp
    
    tools_dict = {}
    if hasattr(mcp, "_local_provider") and hasattr(mcp._local_provider, "_components"):
        tools_dict = {
            v.name: v
            for k, v in mcp._local_provider._components.items()
            if k.startswith("tool:")
        }
    else:
        tool_manager = getattr(mcp, "_tool_manager", None) or getattr(mcp, "_tools", None)
        tools_dict = tool_manager._tools
    
    for name, tool in tools_dict.items():
        try:
            # call fn with no arguments
            tool.fn()
        except ValidationError:
            # Expected for tools with required fields
            pass
        except TypeError as e:
            pytest.fail(f"Tool {name} raised TypeError on empty args: {e}")
        except Exception:
            # Other exceptions (like workspace dir not existing or persistence disabled)
            # are expected because we call the function directly with no setup.
            pass