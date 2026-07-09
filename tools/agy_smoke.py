"""Smoke test script for agy-mcp-server.

Runs agy_self_test to inspect all registered tools and ensure metadata integrity.
"""

import os
import sys


def run_smoke_test() -> None:
    """Executes the self-test metadata check and validates properties."""
    try:
        # Resolve repo root and insert the src/ path to sys.path
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        src_dir = os.path.join(repo_root, "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

        from agy_mcp_server.server import agy_self_test

        # Execute agy_self_test with no arguments
        r = agy_self_test()

        # Verify assertions
        assert r.total_tools >= 14, f"total_tools {r.total_tools} is less than 14"
        assert r.tolerant_count == r.total_tools, (
            f"tolerant_count {r.tolerant_count} != total_tools {r.total_tools}"
        )
        assert ("14 tools" in r.summary) or ("total: 14" in r.summary), (
            f"expected substring not found in summary: {r.summary!r}"
        )

        print(f"AGY_SMOKE_OK total={r.total_tools} tolerant={r.tolerant_count}")
        sys.exit(0)

    except Exception as e:
        print(f"AGY_SMOKE_FAIL {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_smoke_test()
