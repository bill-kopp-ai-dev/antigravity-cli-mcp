"""Tests for the self-test tool and req-optional behavior."""

import pytest
from agy_mcp_server.server import agy_self_test
from agy_mcp_server.models import AgySelfTestRequest


def test_self_test_returns_expected_shape():
    """agy_self_test returns a valid report with the expected fields."""
    result = agy_self_test()
    assert result.total_tools >= 13  # at least the 13 we registered
    assert isinstance(result.tolerant_count, int)
    assert isinstance(result.requires_req_count, int)
    assert len(result.tools) == result.total_tools
    # After the refactor, ALL tools should be tolerant to args={}
    assert result.requires_req_count == 0, (
        f"Some tools still require `req` wrapper: "
        f"{[r.name for r in result.tools if r.requires_req_wrapper]}"
    )
    assert result.tolerant_count == result.total_tools


def test_self_test_with_filter():
    """agy_self_test supports include filter."""
    result = agy_self_test(req=AgySelfTestRequest(include=["agy_health"]))
    assert all(r.name.startswith("agy_health") for r in result.tools)
