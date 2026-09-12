"""The worker as an allocation. М10's `VmsWorker`, unchanged, plus what
Nomad hands a process and what a server knows about itself:

    NOMAD_ALLOC_INDEX   -> the slot to claim: w-<index>. The index is the preference;
                           the claim (CAS on vms/slots/w-N) is the proof. Two allocations
                           with one index — the documented bug — resolve at the CAS.
    NOMAD_NODE_NAME     -> `server` in the heartbeat: which resource it records into
    NOMAD_META_labels   -> `labels` in the heartbeat: what this server can reach
                           ("vlan:cctv-a,vlan:cctv-b"); the controller places by them
    CAPACITY            -> the worker's own number, from М9 Lesson 7's probe on THIS server

Nothing here is new behaviour. A worker on a cluster is a worker on a box
whose stores happen to be raft and MinIO.
"""
from __future__ import annotations

import os
import socket
import time

from vms.worker import FakeActuator, VmsWorker


def slot_from_environment(env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    if env.get("WORKER_NAME"):
        return env["WORKER_NAME"]
    if "NOMAD_ALLOC_INDEX" in env:
        return f"w-{int(env['NOMAD_ALLOC_INDEX'])}"
    return None                                  # claim whatever is free — a lapsed slot first


def labels_from_environment(env: dict | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [l for l in env.get("NOMAD_META_labels", "").split(",") if l]


class ClusterWorker(VmsWorker):
    def __init__(self, vars_, objects, actuator=None, env: dict | None = None, **kw):
        env = dict(os.environ if env is None else env)
        kw.setdefault("server", env.get("NOMAD_NODE_NAME") or env.get("NOMAD_NODE_ID") or socket.gethostname())
        kw.setdefault("capacity", int(env.get("CAPACITY", "50")))
        kw.setdefault("instance", env.get("NOMAD_ALLOC_ID") or None)
        super().__init__(slot_from_environment(env), vars_, objects, actuator or FakeActuator(), **kw)
        self.labels = labels_from_environment(env)
        self.alloc = env.get("NOMAD_ALLOC_ID", "")
        self._started_wall = self.wall()
        # the previous instance of this slot, if it left a heartbeat: what failover is measured from
        self.previous_hb, self.previous_instance = 0.0, ""
        raw = objects.get(self.sub.heartbeat_key(self.name))
        if raw:
            from vmsplatform.contract import Heartbeat
            old = Heartbeat.from_bytes(raw)
            if old.extra.get("instance") != self.instance:
                self.previous_hb, self.previous_instance = old.ts, old.extra.get("instance", "")

    def heartbeat_once(self) -> None:
        self.heartbeat(self.status(), server=self.server, instance=self.instance, alloc=self.alloc,
                       labels=",".join(self.labels), assignment_rev=self.assignment_rev,
                       fenced=not self.recording_allowed, conflicts=self.conflicts(), passes=self.passes,
                       capacity=self.capacity, headroom=self.headroom(), started=self._started_wall,
                       previous_hb=self.previous_hb, previous_instance=self.previous_instance)
