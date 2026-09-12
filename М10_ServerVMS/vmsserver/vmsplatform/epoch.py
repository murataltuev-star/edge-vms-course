"""The fencing token and the lease — generic to any writer that can have
two instances. The subsystem decides what key the epoch goes in; the
platform only promises that it comes from one issuer and increases.

    next_epoch(vars, key)   issue the next epoch for `key` by check-and-set: two callers
                            racing get two different numbers, in order
    Lease                   may_write while now − last_renewal < TTL − margin, on a
                            monotonic clock; renew = read the key and find it still mine
"""
from __future__ import annotations

import time

from .variables import Conflict, Variables


def next_epoch(vars_: Variables, key: str, retries: int = 200) -> tuple[int, int]:
    for _ in range(retries):
        items, idx = vars_.get(key)
        current = int(items["epoch"]) if items else 0
        try:
            new_idx = vars_.put(key, {"epoch": current + 1}, cas=idx)
            return current + 1, new_idx
        except Conflict:
            continue
    raise RuntimeError(f"could not issue an epoch for {key} after {retries} conflicts")


def current_epoch(vars_: Variables, key: str) -> int:
    items, _ = vars_.get(key)
    return int(items["epoch"]) if items else 0


class Lease:
    def __init__(self, vars_: Variables, key: str, epoch: int, ttl: float = 30.0, margin: float = 5.0,
                 clock=time.monotonic):
        self.vars, self.key, self.epoch = vars_, key, epoch
        self.ttl, self.margin, self.clock = ttl, margin, clock
        self.last_renewal = clock()
        self.fenced = False
        self.conflicts = 0

    def renew(self) -> bool:
        if self.fenced:
            return False
        try:
            live = current_epoch(self.vars, self.key)
        except Exception:                      # noqa: BLE001 — the store is unreachable; keep going until TTL − margin
            return self.may_write()
        if live != self.epoch:
            self.fenced, self.conflicts = True, self.conflicts + 1
            return False
        self.last_renewal = self.clock()
        return True

    def may_write(self) -> bool:
        return not self.fenced and (self.clock() - self.last_renewal) < (self.ttl - self.margin)

    def seconds_left(self) -> float:
        return max(0.0, (self.ttl - self.margin) - (self.clock() - self.last_renewal))
