"""Lesson 3 — the camera list, and where the console gets it.

Not a fan-out to N Node consoles (waits for the slowest, breaks on the first
dead Node). Not status in Variables (raft is not for frequent, large data).
The read model reads what each Node already publishes beside its heartbeat
— <node>/heartbeat in the cluster's object store, carrying the Node's own
/status as `cameras` — holds it in memory, and serves the list, search and
pagination from there. No call to any Node on any request. It is a cache
that admits to being one: a restart is one pass over the objects.

Staleness is shown, never hidden: every row carries the age of its snapshot;
a Node older than `lost_after` is *unreachable — last known state*, its
cameras still listed. A cluster that did not answer is reported as such,
with its rows from the last successful pass — never as an empty cluster.

Grouped by failure domain: the heartbeat carries the server the Node runs
on, so when a server dies its Nodes go silent together and the console
shows ONE cause, not thirty greyed cameras.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .federation import Federation, Unreachable


@dataclass
class Row:
    camera: int
    name: str
    site: str
    node: str
    cluster: str
    server: str
    phase: str
    conditions: dict
    revision: int
    epoch: int
    age: float                  # seconds since the snapshot this row came from
    node_state: str             # "live" | "stale" | "unreachable" (cluster did not answer)

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        d["as_of"] = f"as of {self.age:.0f} s ago" if self.node_state == "live" else f"{self.node_state} — last known state, {self.age:.0f} s old"
        return d


@dataclass
class Cause:
    scope: str                  # "cluster" | "server" | "node"
    name: str
    silent_for: float
    nodes: list[str]
    cameras: int

    def sentence(self) -> str:
        what = {"cluster": "cluster unreachable", "server": "server silent", "node": "Node silent"}[self.scope]
        return f"{what}: {self.name} for {self.silent_for:.0f} s — {len(self.nodes)} Node(s), {self.cameras} camera(s)"


@dataclass
class Snapshot:
    node: str
    cluster: str
    ts: float
    epoch: int
    server: str
    cameras: list[dict]
    revision: int = 0


class ReadView:
    def __init__(self, fed: Federation, lost_after: float = 45.0, wall=time.time):
        self.fed, self.lost_after, self.wall = fed, lost_after, wall
        self.snapshots: dict[str, Snapshot] = {}      # node -> last snapshot seen (kept across failed passes)
        self.cluster_ok: dict[str, float] = {}         # cluster -> wall time of its last successful pass
        self.cluster_down_since: dict[str, float] = {}
        self.passes = 0

    # -- the one pass ----------------------------------------------------------
    def refresh(self) -> None:
        now = self.wall()
        for name, c in self.fed.clusters.items():
            try:
                nodes = c.directory(ttl=0).scan(force=True)
                for node in nodes:
                    raw = c.objects.get(f"{node}/heartbeat")
                    if not raw:
                        continue
                    hb = json.loads(raw)
                    self.snapshots[node] = Snapshot(node, name, float(hb.get("ts", 0)), int(hb.get("epoch", 0)),
                                                    str(hb.get("server", "?")), list(hb.get("cameras", [])),
                                                    int(hb.get("revision", 0)))
            except Unreachable:
                self.cluster_down_since.setdefault(name, now)
                continue
            self.cluster_ok[name] = now
            self.cluster_down_since.pop(name, None)
        self.passes += 1

    # -- reads, from memory ----------------------------------------------------
    def rows(self) -> list[Row]:
        now = self.wall()
        out = []
        for s in self.snapshots.values():
            age = max(0.0, now - s.ts)
            if s.cluster in self.cluster_down_since:
                state = "unreachable"
            elif age > self.lost_after:
                state = "stale"
            else:
                state = "live"
            for cam in s.cameras:
                out.append(Row(int(cam["id"]), cam.get("name", ""), cam.get("site", ""), s.node, s.cluster, s.server,
                               cam.get("phase", "?"), cam.get("conditions", {}), int(cam.get("revision", 0)),
                               s.epoch, age, state))
        out.sort(key=lambda r: (r.cluster, r.node, r.camera))
        return out

    def list(self, q: str = "", page: int = 1, size: int = 50, cluster: str | None = None) -> dict:
        rows = [r for r in self.rows() if (not cluster or r.cluster == cluster)
                and (not q or q.lower() in r.name.lower() or q == str(r.camera) or q.lower() in r.site.lower())]
        total = len(rows)
        page_rows = rows[(page - 1) * size: page * size]
        return {"total": total, "page": page, "size": size, "rows": [r.to_json() for r in page_rows],
                "clusters": {n: ("unreachable" if n in self.cluster_down_since else "ok") for n in self.fed.clusters},
                "complete": not self.cluster_down_since}

    def causes(self) -> list[Cause]:
        """Silence grouped by the largest failure domain that explains it."""
        now = self.wall()
        causes: list[Cause] = []
        for cl, since in self.cluster_down_since.items():
            nodes = [s for s in self.snapshots.values() if s.cluster == cl]
            causes.append(Cause("cluster", cl, now - since, sorted(s.node for s in nodes), sum(len(s.cameras) for s in nodes)))
        silent = [s for s in self.snapshots.values() if s.cluster not in self.cluster_down_since and now - s.ts > self.lost_after]
        by_server: dict[tuple[str, str], list[Snapshot]] = {}
        for s in silent:
            by_server.setdefault((s.cluster, s.server), []).append(s)
        for (cl, server), group in by_server.items():
            all_on_server = [s for s in self.snapshots.values() if s.cluster == cl and s.server == server]
            silent_for = now - max(s.ts for s in group)
            if len(group) == len(all_on_server) and len(group) > 1:
                causes.append(Cause("server", f"{cl}/{server}", silent_for, sorted(s.node for s in group), sum(len(s.cameras) for s in group)))
            else:
                for s in group:
                    causes.append(Cause("node", s.node, now - s.ts, [s.node], len(s.cameras)))
        return sorted(causes, key=lambda c: (-c.cameras, c.name))
