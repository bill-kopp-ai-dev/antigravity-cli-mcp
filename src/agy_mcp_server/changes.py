from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileState:
    size: int
    mtime_ns: int
    sha256: str | None


def is_git_repo(workspace: Path) -> bool:
    return (workspace / ".git").exists()


def git_diff(workspace: Path) -> str | None:
    if not is_git_repo(workspace):
        return None
    try:
        return subprocess.check_output(
            ["git", "diff", "--no-color"],
            cwd=str(workspace),
            text=True,
        )
    except Exception:
        return None


def git_changed_files(workspace: Path) -> list[str]:
    if not is_git_repo(workspace):
        return []
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            text=True,
        )
    except Exception:
        return []
    files: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.add(path)
    return sorted(files)


def snapshot_tree(
    workspace: Path | str, *, ignore_dir_names: set[str], max_file_bytes: int
) -> dict[str, FileState]:
    workspace = Path(workspace).resolve()
    result: dict[str, FileState] = {}

    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in ignore_dir_names]
        for filename in filenames:
            full = Path(dirpath) / filename
            try:
                if full.is_symlink():
                    continue
                st = full.stat()
            except OSError:
                continue

            rel = full.relative_to(workspace).as_posix()
            sha = _sha256_if_small(full, st.st_size, max_file_bytes)
            result[rel] = FileState(size=st.st_size, mtime_ns=st.st_mtime_ns, sha256=sha)

    return result


def diff_snapshots(before: dict[str, FileState], after: dict[str, FileState]) -> list[str]:
    changed: list[str] = []
    keys = set(before.keys()) | set(after.keys())
    for k in keys:
        a = before.get(k)
        b = after.get(k)
        if a is None or b is None:
            changed.append(k)
            continue
        if a.size != b.size:
            changed.append(k)
            continue
        if a.sha256 is not None and b.sha256 is not None:
            if a.sha256 != b.sha256:
                changed.append(k)
            continue
        if a.mtime_ns != b.mtime_ns:
            changed.append(k)
            continue

    return sorted(set(changed))


def _sha256_if_small(path: Path, size: int, max_file_bytes: int) -> str | None:
    if size > max_file_bytes:
        return None
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(128 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
