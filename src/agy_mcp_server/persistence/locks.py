"""Thread-safe lock for serializing persistence writes."""

from __future__ import annotations

import threading

# Single global lock. Persistence operations are infrequent (a few KB
# per write) so contention is not a concern. One lock keeps reasoning
# about the file system simple.
_persistence_lock = threading.Lock()


def persistence_lock() -> threading.Lock:
    """Return the global persistence lock."""
    return _persistence_lock