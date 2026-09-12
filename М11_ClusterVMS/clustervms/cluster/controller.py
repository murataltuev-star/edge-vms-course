"""The controller as a job. М10's `VmsController` plus the two things a
cluster adds to placement and the one thing it owes the domain:

    constraints   a camera's `labels` ("vlan:cctv-a") must be a subset of what the worker's
                  server reports; "the system is full" now also means "nothing that can reach it"
    servers       the heartbeat says which server a worker is on; the placement reason names it
    the snapshot  vms/snapshot in the object store — cameras and placement as one object for
                  М12's read model. The only thing that leaves the cluster, and it is a copy.

Still no Nomad client: it never places a process, never sets `count`, never
retires a slot from a silence.
"""
from __future__ import annotations

import json
import time

from vms.controller import Placement, VmsController
from vmsplatform.contract import Heartbeat


class ClusterController(VmsController):
    def __init__(self, vars_, objects, capacity: int = 50, wall=time.time, cluster: str = "cluster-a"):
        super().__init__(vars_, objects, capacity, wall)
        self.cluster = cluster

    # -- constraints ----------------------------------------------------------------------
    def labels_of(self, worker: str) -> set[str]:
        hb = self.workers_seen(max_age=1e12).get(worker)
        return set(l for l in hb.extra.get("labels", "").split(",") if l) if hb else set()

    def server_of(self, worker: str) -> str:
        hb = self.workers_seen(max_age=1e12).get(worker)
        return hb.extra.get("server", "?") if hb else "?"

    def eligible(self, cam: dict, workers: list[str]) -> list[str]:
        need = set(cam.get("labels") or [])
        return [w for w in workers if need <= self.labels_of(w)]

    def place(self, cid: int, workers: list[str] | None = None) -> Placement | None:
        have = self.placement(cid)
        if have:
            return have
        cam = self.camera(cid)
        if cam is None:
            return None
        pool = sorted(workers if workers is not None else self.workers_seen())
        pool = self.eligible(cam, pool)
        if not pool:
            return None                          # "the system is full" — or nothing that can reach it
        best, free = None, 0
        for w in pool:
            f = self.capacity_of(w) - self.load(w)
            if f > free:
                best, free = w, f
        if best is None:
            return None
        reason = (f"most free capacity ({free}) among {len(pool)} worker(s)"
                  + (f" reaching {','.join(sorted(cam['labels']))}" if cam.get("labels") else "")
                  + f"; on {self.server_of(best)}")
        pl = Placement(cid, best, reason, self.wall(), 0)

        def mutate(it):
            if it and it.get("worker"):
                return None
            return {"worker": pl.worker, "reason": pl.reason, "at": pl.at, "rev": int(it.get("rev", 0)) + 1 if it else 1}
        written = self.write(self.sub.config("placement", str(cid)), mutate)
        pl = Placement(cid, written["worker"], written["reason"], float(written["at"]), int(written["rev"]))
        self.assign_add(pl.worker, str(cid))
        return pl

    def unplaceable(self) -> list[dict]:
        """Cameras nothing can reach — the console's honest answer, with the labels named."""
        live = sorted(self.workers_seen())
        return [{"id": c["id"], "labels": c.get("labels", []), "workers_live": len(live)}
                for c in self.cameras() if self.placement(c["id"]) is None and not self.eligible(c, live)]

    # -- what leaves the cluster ----------------------------------------------------------
    def snapshot(self) -> dict:
        """Cameras and placement as one object: what М12 reads. A copy with an
        age — never the rows themselves, which do not leave raft."""
        return {"cluster": self.cluster, "ts": self.wall(),
                "cameras": [{**c, "worker": self.where(c["id"]), "server": self.server_of(self.where(c["id"]) or "")}
                            for c in self.cameras()]}

    def publish_snapshot(self) -> None:
        self.objects.put(self.sub.config("snapshot"), json.dumps(self.snapshot()).encode())

    # -- the numbers this module exports --------------------------------------------------------
    def failover_seconds(self) -> dict[str, float]:
        """Per worker: the gap between the heartbeat before its current instance started
        and that instance's first — measured from what the workers wrote, not from Nomad."""
        out = {}
        for w, hb in self.workers_seen(max_age=1e12).items():
            started = float(hb.extra.get("started", hb.ts))
            prev = float(hb.extra.get("previous_hb", 0) or 0)
            if prev:
                out[w] = round(started - prev, 1)
        return out


def heartbeats(objects, prefix: str = "vms/") -> dict[str, Heartbeat]:
    """Every worker's last heartbeat, whatever its age — the console's read model."""
    out = {}
    for key in objects.list(prefix):
        if key.endswith("/heartbeat") and not key.startswith(prefix + "resources/"):
            raw = objects.get(key)
            if raw:
                hb = Heartbeat.from_bytes(raw)
                out[hb.worker] = hb
    return out
