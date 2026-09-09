"""A faithful stand-in for the two Nomad primitives Lesson 28 compares.

Nomad Variables (HTTP API): every variable carries a raft-assigned
ModifyIndex. PUT ?cas=<index> succeeds only if the stored ModifyIndex still
equals <index>; otherwise 409 Conflict. The index is monotonic across the
whole raft log, never reused, and survives losing any one server.

Nomad Variable Locks: acquire returns an opaque lock ID (a UUID) and a TTL.
Renew/release need that ID. There is no ordering between successive lock
IDs — which is exactly the property a fencing token needs and a lock lacks.

Both are simulated with the semantics the docs describe. Nothing here is
Nomad; everything here is what Nomad promises.
"""
from __future__ import annotations

import threading
import time
import uuid


class Conflict(Exception):
    """HTTP 409: the cas index did not match the current ModifyIndex."""


class Variables:
    def __init__(self):
        self._lock = threading.Lock()
        self._raft_index = 1000          # one log for the whole cluster
        self._items: dict[str, tuple[dict, int]] = {}   # path -> (items, ModifyIndex)

    def get(self, path: str):
        with self._lock:
            if path not in self._items:
                return None, 0
            items, idx = self._items[path]
            return dict(items), idx

    def put(self, path: str, items: dict, cas: int | None = None) -> int:
        """Returns the new ModifyIndex. With cas, atomic compare-and-set."""
        with self._lock:
            _, current = self._items.get(path, (None, 0))
            if cas is not None and cas != current:
                raise Conflict(f"cas={cas} but ModifyIndex={current}")
            self._raft_index += 1
            self._items[path] = (dict(items), self._raft_index)
            return self._raft_index


class VariableLock:
    """acquire -> opaque ID + TTL. Renew and release need the ID. That's all."""

    def __init__(self, ttl: float = 15.0):
        self.ttl = ttl
        self._holder: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> str | None:
        with self._lock:
            now = time.monotonic()
            if self._holder and now < self._expires:
                return None
            self._holder = str(uuid.uuid4())
            self._expires = now + self.ttl
            return self._holder

    def renew(self, lock_id: str) -> bool:
        with self._lock:
            if lock_id != self._holder:
                return False
            self._expires = time.monotonic() + self.ttl
            return True

    def release(self, lock_id: str) -> bool:
        with self._lock:
            if lock_id != self._holder:
                return False
            self._holder = None
            return True


# ---- the epoch issuer, built on CAS -----------------------------------

def next_epoch(vars_: Variables, node: str, retries: int = 10) -> tuple[int, int]:
    """Issue the next epoch for `node`. Returns (epoch, ModifyIndex).
    Two callers racing get two DIFFERENT epochs, in order; the loser of the
    CAS re-reads and goes again. Nobody ever receives the same number."""
    path = f"nodes/{node}/epoch"
    for _ in range(retries):
        items, idx = vars_.get(path)
        current = int(items["epoch"]) if items else 0
        try:
            new_idx = vars_.put(path, {"epoch": str(current + 1)}, cas=idx)
            return current + 1, new_idx
        except Conflict:
            continue                                   # 409: re-read and retry
    raise RuntimeError("could not issue an epoch")
