from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def ensure_valid_mcp_config_json(path: Path) -> bool:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        _atomic_write_json(path, {"mcpServers": {}})
        return True

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    if not content.strip():
        _atomic_write_json(path, {"mcpServers": {}})
        return True

    try:
        json.loads(content)
        return False
    except json.JSONDecodeError:
        return False


def _atomic_write_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as f:
        tmp = Path(f.name)
        f.write(payload)
        f.flush()
    tmp.replace(path)
