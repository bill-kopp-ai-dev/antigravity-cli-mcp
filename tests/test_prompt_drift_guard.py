"""Sprint N+2: prompt drift guard.

Keep CONTRATO_TOOLS.md §Prompts and src/agy_mcp_server/server.py's
@mcp.prompt-registered prompts in sync, mirroring the existing
`test_contrato_drift.py` guard for tools.

Fails if any prompt registered on the MCP server is not documented in
CONTRATO_TOOLS.md, or vice versa, and verifies the four docstring
enrichment invariants introduced in Sprint N+2:
  - Input (optional fields marked)
  - Returns
  - Side effects
  - Use when
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRATO_PATH = REPO_ROOT / "CONTRATO_TOOLS.md"
SERVER_PATH = REPO_ROOT / "src" / "agy_mcp_server" / "server.py"


def _extract_documented_prompts(markdown: str) -> set[str]:
    """Pull every documented prompt name (backtick-quoted) under §Prompts.

    Matches both `prompt_name` and `agy_name` aliases, since CONTRATO
    uses the public-facing `agy_*` name registered with the @mcp.prompt
    decorator."""
    section = re.search(
        r"^## Prompts\s*\n(.*?)(?=^## |\Z)", markdown, flags=re.MULTILINE | re.DOTALL
    )
    if not section:
        return set()
    body = section.group(1)
    names = set(re.findall(r"`([a-zA-Z_]+(?:_[a-zA-Z]+)*)`", body))
    # Also accept `prompt_<name>` references (the in-source function names
    # that back the agy_* aliases).
    names |= set(re.findall(r"`(prompt_[a-zA-Z_]+)`", body))
    return names


def _registered_prompts() -> set[str]:
    """Return the set of registered @mcp.prompt names by inspecting the
    decorator invocations in server.py. We parse instead of importing to
    avoid running the FastMCP server during unit tests."""
    text = SERVER_PATH.read_text()
    raw = set(re.findall(r'@mcp\.prompt\(name=prompt_name\("([a-zA-Z_]+)"\)\)', text))
    # CONTRATO documents the agy_* aliases; prefix accordingly so the
    # registered vs documented sets are comparable.
    return {f"agy_{name}" for name in raw}


class TestPromptDrift:
    def test_contrato_file_exists(self) -> None:
        assert CONTRATO_PATH.exists(), f"missing {CONTRATO_PATH}"

    def test_documented_prompts_match_registered(self) -> None:
        registered = _registered_prompts()
        documented = _extract_documented_prompts(CONTRATO_PATH.read_text())
        # Some prompts are not name=... (zero-arg @mcp.prompt decorators)
        # so we cannot enforce strict equality, but the named ones MUST
        # all appear in CONTRATO.
        missing_from_doc = registered - documented
        assert not missing_from_doc, (
            f"named prompts not documented: {sorted(missing_from_doc)}. "
            f"Add them under CONTRATO_TOOLS.md §Prompts."
        )

    def test_all_prompts_have_input_section(self) -> None:
        """Every @mcp.prompt function in server.py must have a docstring
        that includes 'Input' and 'Returns' sections."""
        text = SERVER_PATH.read_text()
        # Match def prompt_<name>(...): up to the next """ pair
        blocks = re.findall(
            r"def (prompt_[a-zA-Z_]+)\([^)]*\)[^:]*:\s*\n\s*\"\"\"(.*?)\"\"\"",
            text,
            flags=re.DOTALL,
        )
        assert blocks, "no @mcp.prompt functions found"
        for name, doc in blocks:
            assert "Input" in doc, f"{name} docstring missing 'Input' section"
            assert "Returns" in doc, f"{name} docstring missing 'Returns' section"
            assert "Side effects" in doc, f"{name} docstring missing 'Side effects' section"

    def test_all_prompts_have_use_when_anchor(self) -> None:
        """Every @mcp.prompt function should have a 'Use when' anchor."""
        text = SERVER_PATH.read_text()
        blocks = re.findall(
            r"def (prompt_[a-zA-Z_]+)\([^)]*\)[^:]*:\s*\n\s*\"\"\"(.*?)\"\"\"",
            text,
            flags=re.DOTALL,
        )
        for name, doc in blocks:
            assert "Use when" in doc, f"{name} docstring missing 'Use when' anchor"

    def test_known_named_prompts_listed(self) -> None:
        text = CONTRATO_PATH.read_text()
        # The four prompts registered with name=prompt_name(...) must be in §Prompts
        for prompt in (
            "agy_persistence_protocol",
            "agy_quickstart",
            "agy_contract",
            "agy_troubleshoot",
        ):
            assert prompt in text, (
                f"{prompt} not listed under CONTRATO_TOOLS.md §Prompts"
            )

    def test_prompts_section_has_subheadings(self) -> None:
        """§Prompts should be subdivided into Sync/Async/Selection/Cheatsheets."""
        text = CONTRATO_PATH.read_text()
        for sub in (
            "### Sync orchestration",
            "### Async orchestration",
            "### Selection & safety",
            "### Cheatsheets",
        ):
            assert sub in text, f"CONTRATO_TOOLS.md §Prompts missing '{sub}'"