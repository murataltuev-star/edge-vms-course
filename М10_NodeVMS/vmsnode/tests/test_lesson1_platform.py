"""Lesson 1 — the subsystem contract, and the platform that knows nothing."""
import os
import threading
from vmsplatform.contract import Assignment, Controller, Heartbeat, Subsystem, Worker
from vmsplatform.epoch import Lease, current_epoch, next_epoch
from vmsplatform.variables import Conflict, FileVariables, Forbidden
from tests.conftest import Box, Clock


def test_the_config_store_survives_a_restart_and_refuses_a_stale_cas():
    box = Box()
    idx = box.vars.put("vms/cameras/7", {"name": "gate", "revision": 1}, cas=0)
    assert idx == 1001
    again = FileVariables(box.vars.root)                       # a new process, same directory
    items, idx2 = again.get("vms/cameras/7")
    assert items == {"name": "gate", "revision": "1"} and idx2 == idx
    try:
        again.put("vms/cameras/7", {"name": "x"}, cas=idx - 1); raise AssertionError("must conflict")
    except Conflict:
        pass
    assert again.put("vms/cameras/7", {"name": "x"}, cas=idx) == 1002
    assert again.list("vms/") == ["vms/cameras/7"] and again.get("nope") == (None, 0)


def test_two_processes_one_cas_winner():
    box = Box()
    results = []
    def race(n):
        v = FileVariables(box.vars.root)                       # each "process" opens the store itself
        for _ in range(40):
            items, idx = v.get("counter")
            try:
                v.put("counter", {"n": int(items["n"]) + 1 if items else 1}, cas=idx); results.append(n)
            except Conflict:
                pass
    ts = [threading.Thread(target=race, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    n = int(box.vars.get("counter")[0]["n"])
    assert n == len(results)                                   # every successful write counted exactly once


def test_one_writer_per_prefix():
    box = Box()
    ctl = box.vars.as_writer("vmscontroller", ["vms/*"])
    wrk = box.vars.as_writer("vmsworker-1", ["vms/epoch/*"])
    ctl.put("vms/cameras/7", {"name": "gate"})
    wrk.put("vms/epoch/7", {"epoch": 1})
    try:
        wrk.put("vms/cameras/7", {"name": "mine now"}); raise AssertionError("a worker never writes configuration")
    except Forbidden:
        pass


def test_epoch_issuer_never_reuses_a_number():
    box = Box()
    issued = []
    def race():
        v = FileVariables(box.vars.root)
        for _ in range(25):
            issued.append(next_epoch(v, "vms/epoch/7")[0])
    ts = [threading.Thread(target=race) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(issued) == list(range(1, 101)) and current_epoch(box.vars, "vms/epoch/7") == 100


def test_lease_on_a_monotonic_clock():
    box = Box(); clk = Clock()
    e, _ = next_epoch(box.vars, "vms/epoch/7")
    lease = Lease(box.vars, "vms/epoch/7", e, ttl=30, margin=5, clock=clk)
    clk.advance(24.9); assert lease.may_write()
    clk.advance(0.2);  assert not lease.may_write() and lease.seconds_left() == 0
    assert lease.renew() and lease.may_write()
    next_epoch(box.vars, "vms/epoch/7")                        # somebody else took camera 7
    assert lease.renew() is False and lease.fenced and lease.conflicts == 1


def test_the_platform_knows_nothing_about_video():
    """No import from vms/ anywhere under vmsplatform/."""
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vmsplatform")
    for f in os.listdir(here):
        if f.endswith(".py"):
            src = open(os.path.join(here, f)).read()
            assert "from vms" not in src and "import vms" not in src, f
            if f != "__init__.py":
                assert "camera" not in src.lower(), f              # not even the word
    sub = Subsystem("vms")
    assert sub.assignment("w-1") == "vms/workers/w-1" and sub.epoch_key("7") == "vms/epoch/7"
    assert sub.heartbeat_key("w-1") == "vms/w-1/heartbeat" and sub.acl_controller() == ["vms/*"]


def test_controller_and_worker_bases_speak_only_the_contract():
    box = Box()
    sub = Subsystem("thing")
    ctl = Controller(sub, box.vars, box.objects, wall=box.wall)
    w = Worker(sub, "t-1", box.vars, box.objects, clock=box.clock, wall=box.wall)
    assert ctl.workers_seen() == {}                            # nobody has heartbeaten
    w.heartbeat([{"id": 1, "phase": "running"}], server="srv-1")
    seen = ctl.workers_seen()
    assert list(seen) == ["t-1"] and seen["t-1"].extra["server"] == "srv-1"
    a = ctl.assign("t-1", ["3", "1", "2"])
    assert a.units == ["1", "2", "3"] and a.rev == 1 and w.assignment().units == ["1", "2", "3"]
    assert ctl.assign("t-1", ["1"]).rev == 2
    assert w.take_epoch("1") == 1 and w.may_write("1")
    box.wall.advance(100)
    assert ctl.workers_seen(max_age=45) == {}                  # a silent worker is not a worker
