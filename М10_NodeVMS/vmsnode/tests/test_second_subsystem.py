"""Lesson 5 — the second subsystem: a controller and a worker that count
seconds, through the same platform code, with a different prefix. If this
works, the VMS is a subsystem and not the platform."""
from vmsplatform.contract import Controller, Subsystem, Worker
from vmsplatform.events import EventLog, read_bucket, subsystems_under
from tests.conftest import Box
import os

COUNTER = Subsystem("counter")


class CounterController(Controller):
    def __init__(self, box):
        super().__init__(COUNTER, box.vars, box.objects, wall=box.wall)

    def create(self, name: str, step: int) -> str:
        self.vars.put(COUNTER.config("units", name), {"step": step, "revision": 1}, cas=0)
        return name


class CounterWorker(Worker):
    def __init__(self, box, name):
        super().__init__(COUNTER, name, box.vars, box.objects, clock=box.clock, wall=box.wall)
        self.values: dict[str, int] = {}
        self.archive = box.archive                                       # this server's resource, shared with the VMS

    def reconcile_once(self, now=0.0):
        a = self.assignment()
        for unit in a.units:
            if unit not in self.epochs:
                self.take_epoch(unit)
            step = int(self.vars.get(COUNTER.config("units", unit))[0]["step"])
            self.values[unit] = self.values.get(unit, 0) + step
            if self.values[unit] % 10 == 0:                              # an observation, into the counter's own bucket
                EventLog(self.archive, COUNTER.name, unit, self.epochs[unit]).append(self.wall(), "round", value=self.values[unit])
        for unit in list(self.values):
            if unit not in a.units:
                del self.values[unit]; self.release(unit)
        self.heartbeat([{"id": u, "value": v, "phase": "counting"} for u, v in self.values.items()])
        return sorted(self.values)


def test_a_second_subsystem_through_the_same_platform():
    box = Box()
    ctl, w = CounterController(box), CounterWorker(box, "c-1")
    ctl.create("a", 2); ctl.create("b", 5)
    ctl.assign("c-1", ["a", "b"])
    assert w.reconcile_once() == ["a", "b"] and w.reconcile_once() == ["a", "b"]
    assert w.values == {"a": 4, "b": 10} and box.vars.get("counter/epoch/a")[0] == {"epoch": "1"}
    seen = ctl.workers_seen()
    assert seen["c-1"].status == [{"id": "a", "value": 4, "phase": "counting"}, {"id": "b", "value": 10, "phase": "counting"}]
    ctl.assign("c-1", ["b"])
    assert w.reconcile_once() == ["b"] and "a" not in w.epochs
    # its events sit on the same resource under its own prefix, written under its own epoch
    assert subsystems_under(box.archive) == {"counter": ["b"]}
    b = read_bucket(os.path.join(box.archive, "counter", "b", "e1", sorted(os.listdir(os.path.join(box.archive, "counter", "b", "e1")))[0]))
    assert b == [{"t": box.wall(), "kind": "round", "value": 10}]
    # the two subsystems do not see each other: prefixes, and nothing else
    assert box.vars.list("vms/") == [] and box.vars.list("counter/") == ["counter/epoch/a", "counter/epoch/b", "counter/units/a", "counter/units/b", "counter/workers/c-1"]
