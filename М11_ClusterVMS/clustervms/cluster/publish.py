"""Lesson 3 — the one-way publication upward, and what the operator is told.

    object first, then the Variable (by CAS) — a pointer never dangles
    publish on change, with a floor — the RPO is the floor
    acknowledge on local commit, and SHOW durability: `replicated` condition

The directory is each Node's off-box backup. It never writes back.
"""
from __future__ import annotations

import logging
import time

from .variables import Conflict, Variables

log = logging.getLogger("cluster.publish")


class Publisher:
    def __init__(self, node: str, store, vars_: Variables, objects, floor_seconds: float = 5.0,
                 clock=time.monotonic):
        self.node, self.store, self.vars, self.objects = node, store, vars_, objects
        self.floor, self.clock = floor_seconds, clock
        self.published_rev = 0
        self.last_publish = -1e9
        self.last_attempt_failed_since: float | None = None
        self.publishes = 0

    async def publish_once(self) -> bool:
        """Publish if the local revision moved past what the directory holds.
        Returns True if a publish happened (or nothing was needed)."""
        rev = await self.store.config_revision()
        if rev <= self.published_rev:
            return True
        if self.clock() - self.last_publish < self.floor:
            return True                                   # the floor: not yet
        try:
            blob = await self.store.dump_config()
            key = f"{self.node}/rev-{rev}"
            self.objects.put(key, blob)                   # 1. the object, first
            items, idx = self.vars.get(f"nodes/{self.node}")
            items = dict(items or {})
            items.update({"node": self.node, "config": key, "revision": str(rev),
                          "cameras": ",".join(str(c["id"]) for c in await self.store.cameras())})
            self.vars.put(f"nodes/{self.node}", items, cas=idx)   # 2. then the pointer
        except Conflict:
            log.warning("publish: cas conflict on nodes/%s — somebody else wrote our Variable; retrying next tick", self.node)
            return False
        except Exception as e:                            # noqa: BLE001 — the store or the cluster is unreachable
            if self.last_attempt_failed_since is None:
                self.last_attempt_failed_since = self.clock()
            log.warning("publish failed (%s); the Node keeps running, the edit stays local", type(e).__name__)
            return False
        self.published_rev = rev
        self.last_publish = self.clock()
        self.last_attempt_failed_since = None
        self.publishes += 1
        return True

    def replicated(self, local_rev: int) -> tuple[bool, str | None]:
        """The `replicated` condition for the console: status and reason."""
        if local_rev <= self.published_rev:
            return True, None
        if self.last_attempt_failed_since is not None:
            return False, f"not yet replicated: directory unreachable for {self.clock() - self.last_attempt_failed_since:.0f}s"
        return False, f"not yet replicated ({local_rev - self.published_rev} edit(s) pending)"
