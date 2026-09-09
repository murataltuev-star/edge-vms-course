"""Lesson 29 — the cluster directory you already built.

Every Node's Variable lists its camera ids. Scan `nodes/` and you have
answered "where is camera 7" — tens of entries, one raft, strongly
consistent inside the cluster. Cached briefly by the console; invalidated
by time, because there is nothing else to invalidate it with and a few
seconds of staleness is the cost of not reading raft on every page load.
"""
from __future__ import annotations

import time

from .variables import Variables


class Directory:
    def __init__(self, vars_: Variables, ttl: float = 5.0, clock=time.monotonic):
        self.vars, self.ttl, self.clock = vars_, ttl, clock
        self._cache: dict[str, dict] | None = None
        self._at = -1e9

    def scan(self, force: bool = False) -> dict[str, dict]:
        """node id -> {"cameras": [...], "config": ..., "revision": ...}"""
        if not force and self._cache is not None and self.clock() - self._at < self.ttl:
            return self._cache
        out: dict[str, dict] = {}
        for path in self.vars.list("nodes/"):
            if path.count("/") != 1:                       # nodes/<node> only, not nodes/<node>/epoch
                continue
            items, _ = self.vars.get(path)
            if not items or "node" not in items:
                continue
            out[items["node"]] = {
                "cameras": [int(c) for c in items.get("cameras", "").split(",") if c.strip()],
                "config": items.get("config", ""), "revision": int(items.get("revision", "0") or 0)}
        self._cache, self._at = out, self.clock()
        return out

    def where(self, camera_id: int) -> str | None:
        hits = [n for n, d in self.scan().items() if camera_id in d["cameras"]]
        if len(hits) > 1:
            raise RuntimeError(f"camera {camera_id} listed by {hits}: one-writer-per-key is not being enforced")
        return hits[0] if hits else None

    def holdings(self, node: str) -> list[int]:
        return self.scan().get(node, {}).get("cameras", [])
