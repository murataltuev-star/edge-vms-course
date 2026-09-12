"""The subsystem contract — what the platform knows about any subsystem,
and it is all of this:

    a config prefix        <name>/*                      writable by the controller only
    assignment rows        <name>/workers/<worker>       what each worker should run
    a heartbeat object     <name>/<worker>/heartbeat     {ts, status: [...]} — the worker's own report
    an epoch prefix        <name>/epoch/<unit>           fencing tokens the workers take by CAS

`Controller` and `Worker` are the two base classes. The platform never
imports anything from a subsystem; tests/test_second_subsystem.py proves the
shape is generic by running a subsystem that counts seconds through it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .epoch import Lease, next_epoch
from .objects import ObjectStore
from .variables import Conflict, Variables


@dataclass
class Subsystem:
    name: str

    def config(self, *parts: str) -> str:
        return "/".join((self.name,) + parts)

    def assignment(self, worker: str) -> str:
        return f"{self.name}/workers/{worker}"

    def heartbeat_key(self, worker: str) -> str:
        return f"{self.name}/{worker}/heartbeat"

    def epoch_key(self, unit: str) -> str:
        return f"{self.name}/epoch/{unit}"

    def acl_controller(self) -> list[str]:
        return [f"{self.name}/*"]

    def acl_worker(self) -> list[str]:
        return [f"{self.name}/epoch/*"]


@dataclass
class Assignment:
    worker: str
    units: list[str]                 # what the subsystem calls its units of work; the platform does not know
    rev: int = 0

    def to_items(self) -> dict:
        return {"units": ",".join(self.units), "rev": self.rev}

    @classmethod
    def from_items(cls, worker: str, items: dict | None) -> "Assignment":
        if not items:
            return cls(worker, [])
        return cls(worker, [u for u in items.get("units", "").split(",") if u], int(items.get("rev", 0)))


@dataclass
class Heartbeat:
    worker: str
    ts: float
    status: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return json.dumps({"worker": self.worker, "ts": self.ts, "status": self.status, **self.extra}).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Heartbeat":
        d = json.loads(raw)
        extra = {k: v for k, v in d.items() if k not in ("worker", "ts", "status")}
        return cls(d["worker"], float(d["ts"]), list(d.get("status", [])), extra)


class Controller:
    """The only writer of <name>/*. Holds nothing: every method reads the
    store, decides, and writes by CAS. Two instances are harmless."""

    def __init__(self, sub: Subsystem, vars_: Variables, objects: ObjectStore, wall=time.time):
        self.sub, self.vars, self.objects, self.wall = sub, vars_, objects, wall

    def write(self, path: str, mutate, retries: int = 10) -> dict:
        """Read-modify-write by CAS: `mutate(items or {}) -> new items`.
        A conflict means another instance wrote; re-read and go again."""
        for _ in range(retries):
            items, idx = self.vars.get(path)
            new = mutate(dict(items or {}))
            if new is None:
                return dict(items or {})
            try:
                self.vars.put(path, new, cas=idx)
                return new
            except Conflict:
                continue
        raise RuntimeError(f"{path}: {retries} conflicts")

    def workers_seen(self, max_age: float = 45.0) -> dict[str, Heartbeat]:
        """Which workers exist: those that heartbeat recently. Never a list
        the controller keeps — a fact it reads."""
        out = {}
        now = self.wall()
        for key in self.objects.list(self.sub.name + "/"):
            if key.endswith("/heartbeat"):
                raw = self.objects.get(key)
                if raw:
                    hb = Heartbeat.from_bytes(raw)
                    if now - hb.ts <= max_age:
                        out[hb.worker] = hb
        return out

    def assignment(self, worker: str) -> Assignment:
        items, _ = self.vars.get(self.sub.assignment(worker))
        return Assignment.from_items(worker, items)

    def assign(self, worker: str, units: list[str]) -> Assignment:
        def mutate(items):
            rev = int(items.get("rev", 0)) + 1
            return Assignment(worker, sorted(set(units), key=str), rev).to_items()
        return Assignment.from_items(worker, self.write(self.sub.assignment(worker), mutate))

    def assign_add(self, worker: str, unit: str) -> Assignment:
        """Read-modify-write: two controllers adding different units to one
        worker at once both land."""
        def mutate(items):
            a = Assignment.from_items(worker, items)
            if unit in a.units:
                return None
            return Assignment(worker, sorted(set(a.units) | {unit}, key=str), a.rev + 1).to_items()
        return Assignment.from_items(worker, self.write(self.sub.assignment(worker), mutate))

    def assign_remove(self, worker: str, unit: str) -> Assignment:
        def mutate(items):
            a = Assignment.from_items(worker, items)
            if unit not in a.units:
                return None
            return Assignment(worker, [u for u in a.units if u != unit], a.rev + 1).to_items()
        return Assignment.from_items(worker, self.write(self.sub.assignment(worker), mutate))

    def assignments(self) -> dict[str, Assignment]:
        out = {}
        for path in self.vars.list(self.sub.name + "/workers/"):
            worker = path.rsplit("/", 1)[1]
            out[worker] = self.assignment(worker)
        return out


class Worker:
    """Runs its assignment and reports. Reads <name>/workers/<me> and the
    units it names; writes its heartbeat object and, when it starts a unit,
    that unit's epoch by CAS. Never writes configuration. A fresh worker
    rediscovers everything from the store."""

    def __init__(self, sub: Subsystem, name: str, vars_: Variables, objects: ObjectStore,
                 lease_ttl: float = 30.0, lease_margin: float = 5.0, clock=time.monotonic, wall=time.time):
        self.sub, self.name, self.vars, self.objects = sub, name, vars_, objects
        self.clock, self.wall = clock, wall
        self.lease_ttl, self.lease_margin = lease_ttl, lease_margin
        self.epochs: dict[str, int] = {}          # unit -> epoch this worker holds
        self.leases: dict[str, Lease] = {}

    def assignment(self) -> Assignment:
        items, _ = self.vars.get(self.sub.assignment(self.name))
        return Assignment.from_items(self.name, items)

    def take_epoch(self, unit: str) -> int:
        """Called when the worker STARTS a unit: a new epoch, by CAS, and a
        lease on it. A second worker starting the same unit gets the next
        number, and the first one's lease will fence on renewal."""
        epoch, _ = next_epoch(self.vars, self.sub.epoch_key(unit))
        self.epochs[unit] = epoch
        self.leases[unit] = Lease(self.vars, self.sub.epoch_key(unit), epoch, self.lease_ttl, self.lease_margin, self.clock)
        return epoch

    def release(self, unit: str) -> None:
        self.epochs.pop(unit, None)
        self.leases.pop(unit, None)

    def may_write(self, unit: str) -> bool:
        lease = self.leases.get(unit)
        return lease is not None and lease.may_write()

    def renew_leases(self) -> list[str]:
        """Returns the units whose lease was lost — fenced or expired."""
        return [u for u, l in self.leases.items() if not l.renew()]

    def conflicts(self) -> int:
        return sum(l.conflicts for l in self.leases.values())

    def heartbeat(self, status: list[dict], **extra) -> None:
        self.objects.put(self.sub.heartbeat_key(self.name),
                         Heartbeat(self.name, self.wall(), status, extra).to_bytes())

    # what a subsystem implements
    def reconcile_once(self, now: float) -> list:
        raise NotImplementedError
