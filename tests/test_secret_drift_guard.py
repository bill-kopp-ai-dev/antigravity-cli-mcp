"""Security Guard: Secret-drift regression tests and hardcoded secret detector.

This module walks `settings.py` and the source tree to:
1. Parse `src/agy_mcp_server/settings.py` with `ast.parse()` and find all integer
   fields ending with `SECRET_ID` defined with `Field(default=...)`.
2. Verify that each of these fields has a corresponding regression test in the
   `tests/` directory asserting or mentioning its name.
3. Recursively scan `src/agy_mcp_server/` for any hardcoded Contabo secret IDs in
   the range [400000, 500000] and fail if found.

Commands:
    pytest tests/test_secret_drift_guard.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path
import pytest


def parse_secret_ids_from_tree(tree: ast.AST) -> list[str]:
    """Parse class fields ending in SECRET_ID of type int with Field(default=...)."""
    secret_ids = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue

            if not isinstance(item.target, ast.Name):
                continue

            field_name = item.target.id
            if not field_name.upper().endswith("SECRET_ID"):
                continue

            # Check if type annotation is 'int' or contains 'int'
            is_int = False
            if isinstance(item.annotation, ast.Name) and item.annotation.id == "int":
                is_int = True
            else:
                try:
                    if "int" in ast.unparse(item.annotation):
                        is_int = True
                except Exception:
                    pass

            if not is_int:
                continue

            # Check if it has Field(default=...) or Field(default_factory=...)
            if item.value is not None:
                is_field = False
                if (
                    isinstance(item.value, ast.Call)
                    and isinstance(item.value.func, ast.Name)
                    and item.value.func.id == "Field"
                ):
                    for kw in item.value.keywords:
                        if kw.arg in ("default", "default_factory"):
                            is_field = True
                            break
                    if not is_field and len(item.value.args) > 0:
                        is_field = True

                if is_field:
                    secret_ids.append(field_name)

    return secret_ids


def find_tracked_secret_ids(settings_path: Path) -> list[str]:
    """Walk the settings file and return tracked secret IDs."""
    with open(settings_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(settings_path))
    return parse_secret_ids_from_tree(tree)


def find_mentions_in_tests(field_name: str, tests_dir: Path) -> bool:
    """Check if any test file under tests/ mentions the field name."""
    for test_file in tests_dir.glob("test_*.py"):
        try:
            content = test_file.read_text(encoding="utf-8")
            if field_name in content:
                return True
        except Exception:
            pass
    return False


def test_no_tracked_secret_ids_without_regression():
    """Verify that any settings ending in SECRET_ID have regression tests (vacuous today)."""
    # 1. Verify AST machinery works on mock Settings definition
    mock_settings_code = """
class Settings(BaseSettings):
    DUMMY_SECRET_ID: int = Field(default=123456)
    OTHER_FIELD: str = "hello"
    ANOTHER_SECRET: int = Field(default_factory=int)
"""
    tree = ast.parse(mock_settings_code)
    detected = parse_secret_ids_from_tree(tree)
    assert "DUMMY_SECRET_ID" in detected
    assert "OTHER_FIELD" not in detected

    # 2. Run the actual check on settings.py
    settings_path = Path(__file__).parent.parent / "src" / "agy_mcp_server" / "settings.py"
    if not settings_path.exists():
        pytest.fail(f"settings.py not found at {settings_path}")

    tracked_ids = find_tracked_secret_ids(settings_path)

    # 3. Assert all tracked IDs have a corresponding test
    tests_dir = Path(__file__).parent
    missing_tests = []
    for field_name in tracked_ids:
        if not find_mentions_in_tests(field_name, tests_dir):
            missing_tests.append(field_name)

    if missing_tests:
        pytest.fail(
            f"The following SECRET_ID fields in settings.py lack regression tests: {missing_tests}"
        )


def test_no_hardcoded_secret_ids_in_src():
    """Scan src/ for any hardcoded Contabo secret IDs (range 400000-500000)."""
    src_dir = Path(__file__).parent.parent / "src" / "agy_mcp_server"
    if not src_dir.exists():
        pytest.fail(f"Source directory not found at {src_dir}")

    found_violations = []

    for py_file in src_dir.rglob("*.py"):
        if "test" in py_file.name or "test" in str(py_file.parent):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                val = None
                if isinstance(node, ast.Constant):
                    val = node.value

                if isinstance(val, int) and 400000 <= val <= 500000:
                    line_num = getattr(node, "lineno", "unknown")
                    found_violations.append(f"{py_file.name}:{line_num} (value: {val})")
        except Exception as e:
            pytest.fail(f"Failed to parse or read {py_file}: {e}")

    assert not found_violations, (
        f"Found hardcoded Contabo secret IDs (range 400000-500000) in source: {found_violations}"
    )


def test_settings_ast_walker_runs():
    """Sanity check that the walker produces a non-error result on settings.py."""
    settings_path = Path(__file__).parent.parent / "src" / "agy_mcp_server" / "settings.py"
    if not settings_path.exists():
        pytest.fail(f"settings.py not found at {settings_path}")

    try:
        tracked_ids = find_tracked_secret_ids(settings_path)
        assert isinstance(tracked_ids, list)
    except Exception as e:
        pytest.fail(f"AST walker failed on settings.py with error: {e}")
