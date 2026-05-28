from __future__ import annotations

from collections import deque


class RollingTextBuffer:
    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max(1, max_bytes)
        self._chunks: deque[str] = deque()
        self._size = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        self._size += len(text.encode("utf-8", errors="replace"))
        self._trim()

    def get(self) -> str:
        return "".join(self._chunks)

    def tail(self, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""
        data = self.get().encode("utf-8", errors="replace")
        return data[-max_bytes:].decode("utf-8", errors="replace")

    def _trim(self) -> None:
        while self._size > self._max_bytes and self._chunks:
            left = self._chunks.popleft()
            self._size -= len(left.encode("utf-8", errors="replace"))
