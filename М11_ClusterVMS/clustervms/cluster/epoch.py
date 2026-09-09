"""Lesson 28 — the epoch by check-and-set, and the lease that fences.

The epoch is a fencing token: it must come from ONE issuer and it must
INCREASE. A Nomad Variable with cas is both. A variable lock is neither
(its ID is an opaque UUID). A database sequence reissues numbers after a
restore. The archive checks the token by construction: the epoch is in the
path, so a stale instance cannot name the live files.

The lease is what makes the stale instance STOP, on a monotonic clock:
    may write while  now − last_renewal < TTL − margin
Renewal is: read my own epoch Variable and find it still mine. A different
epoch there means a replacement was issued one; that is the moment the
zombie learns what it is, and node_epoch_conflicts is the counter.
"""
from __future__ import annotations

import time

from .variables import Conflict, Variables


def next_epoch(vars_: Variables, node: str, retries: int = 10) -> tuple[int, int]:
    """Issue the next epoch for `node`. Returns (epoch, ModifyIndex). Two
    callers racing get two DIFFERENT epochs, in order; the loser of the CAS
    re-reads and goes again. Nobody ever receives the same number."""
    path = f"nodes/{node}/epoch"
    for _ in range(retries):
        items, idx = vars_.get(path)
        current = int(items["epoch"]) if items else 0
        try:
            new_idx = vars_.put(path, {"epoch": current + 1}, cas=idx)
            return current + 1, new_idx
        except Conflict:
            continue
    raise RuntimeError(f"could not issue an epoch for {node} after {retries} conflicts")


def current_epoch(vars_: Variables, node: str) -> int:
    items, _ = vars_.get(f"nodes/{node}/epoch")
    return int(items["epoch"]) if items else 0


class Lease:
    """Held by the running instance. `renew()` is a read of the epoch
    Variable; `may_write()` is a purely local decision on a monotonic clock."""

    def __init__(self, vars_: Variables, node: str, epoch: int, ttl: float = 30.0,
                 margin: float = 5.0, clock=time.monotonic):
        self.vars, self.node, self.epoch = vars_, node, epoch
        self.ttl, self.margin, self.clock = ttl, margin, clock
        self.last_renewal = clock()
        self.fenced = False
        self.conflicts = 0                         # node_epoch_conflicts

    def renew(self) -> bool:
        """True if the lease still holds. False (and fenced) if the cluster
        issued a newer epoch — or if the read failed and the TTL ran out."""
        if self.fenced:
            return False
        try:
            live = current_epoch(self.vars, self.node)
        except Exception:                          # noqa: BLE001 — the cluster is unreachable; keep going until TTL - margin
            return self.may_write()
        if live != self.epoch:
            self.fenced = True
            self.conflicts += 1
            return False
        self.last_renewal = self.clock()
        return True

    def may_write(self) -> bool:
        if self.fenced:
            return False
        return (self.clock() - self.last_renewal) < (self.ttl - self.margin)

    def seconds_left(self) -> float:
        return max(0.0, (self.ttl - self.margin) - (self.clock() - self.last_renewal))
