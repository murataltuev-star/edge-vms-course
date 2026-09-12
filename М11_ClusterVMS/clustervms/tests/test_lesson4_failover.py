"""Lesson 4 — failover, and the two instances of one worker. The power pull
on a fake clock; the measured number; the old instance waking up; and the
reassignment window, which is the same window with a different verdict."""
from cluster.controller import ClusterController
from vms.worker import FakeActuator
from tests.conftest import Cluster

LOST_AFTER = 45.0


def _recording(n=3):
    c = Cluster(); ctl = ClusterController(c.vars, c.objects, wall=c.wall)
    for i in range(n):
        ctl.create_camera({"source": f"driverpack://file/{i}.mp4"})
    act = FakeActuator(); a = c.worker(1, "srv-a", actuator=act)
    a.heartbeat_once(); ctl.ensure_placed(); a.reconcile_once(); a.heartbeat_once()
    assert act.running == set(range(1, n + 1)) and act.epochs == {i: 1 for i in range(1, n + 1)}
    return c, ctl, a, act


def test_the_power_pull():
    """Server A dies at t=0. Nomad's `disconnect { lost_after = 45s }` starts the
    replacement on B at t=45 (+ a schedule). It claims w-1, reads its assignment,
    takes the next epoch for every camera and records — asking nobody."""
    c, ctl, a, act_a = _recording()
    t_dead = c.wall()
    c.wall.advance(LOST_AFTER + 3)                                        # lost_after, then placement
    act_b = FakeActuator(); b = c.worker(1, "srv-b", actuator=act_b)
    assert b.name == "w-1" and b.previous_instance == a.instance
    assert b.reconcile_once() == [("start", 1), ("start", 2), ("start", 3)]
    assert act_b.epochs == {1: 2, 2: 2, 3: 2} and b.server == "srv-b"
    b.heartbeat_once()
    fo = ctl.failover_seconds()
    assert fo == {"w-1": 48.0}                                             # last heartbeat of A → B's start: the RTO this run
    assert ctl.workers_seen()["w-1"].extra["server"] == "srv-b" and ctl.where(1) == "w-1"   # nothing was rewritten


def test_the_old_instance_wakes_up_and_the_archive_is_intact():
    """Server A was not dead — partitioned, or paused. It comes back with w-1 still
    running epoch 1. Its next renewal finds the slot held by B: fenced at the slot,
    and every epoch says the same. It stops. Its footage is in e1; B's is in e2."""
    c, ctl, a, act_a = _recording()
    c.wall.advance(LOST_AFTER + 3)
    b = c.worker(1, "srv-b"); b.reconcile_once()
    lost = a.lease_pass()                                                  # kill -CONT
    assert not a.recording_allowed and act_a.running == set() and "slot w-1" in a.fenced_reason
    assert a.renew_leases() == ["1", "2", "3"] and a.conflicts() == 3      # the resource-level token agrees, per camera
    a.heartbeat_once()
    hb = ctl.workers_seen(max_age=1e12)                                    # both wrote a heartbeat under one name...
    assert hb["w-1"].extra["fenced"] is True and hb["w-1"].extra["server"] == "srv-a"
    b.heartbeat_once()
    assert ctl.workers_seen()["w-1"].extra["fenced"] is False              # ...and the live one wrote last
    assert a.reconcile_once() == [("failed", 1), ("failed", 2), ("failed", 3)]


def test_the_reassignment_window_is_the_same_window_with_a_different_verdict():
    """The controller moves camera 2 from w-1 to w-2. For up to TTL − margin both
    may write — into different epochs. w-1 loses the lease on 2, sees it is no
    longer assigned, lets it go; it is NOT a zombie and keeps 1 and 3."""
    c, ctl, a, act_a = _recording()
    act_b = FakeActuator(); b = c.worker(2, "srv-b", actuator=act_b); b.heartbeat_once()
    ctl.move(2, "w-2", "operator: srv-b sees that VLAN")
    assert b.reconcile_once() == [("start", 2)] and act_b.epochs[2] == 2   # the destination takes the next epoch
    assert a.lease_pass() == ["2"] and a.recording_allowed and act_a.running == {1, 3}
    assert a.reconcile_once() == [] and ctl.where(2) == "w-2"


def test_the_lease_stops_writing_before_the_replacement_may_start():
    """TTL 30, margin 5: the holder stops at 25 on its own clock; Nomad's lost_after
    is 45. The window between 25 and 45 is the margin the design buys."""
    c, ctl, a, act = _recording(1)
    c.clock.advance(24); assert a.may_write("1")
    c.clock.advance(2);  assert not a.may_write("1")                       # 26 s without a renewal: it stops itself
    assert a.lease_pass() == []                                            # renewal succeeds (nobody took the epoch)...
    assert a.may_write("1")                                                # ...and it may write again — it was never fenced
