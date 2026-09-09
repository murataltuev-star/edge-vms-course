"""Lesson 4 — CAS issuer, ACL, and the lease that fences."""
import threading
from cluster.epoch import Lease, current_epoch, next_epoch
from cluster.variables import Conflict, FakeVariables, Forbidden
from tests.conftest import Clock


def test_cas_issuer_never_reuses_a_number():
    v = FakeVariables()
    issued = []
    def race():
        for _ in range(50):
            issued.append(next_epoch(v, "node-3")[0])
    ts = [threading.Thread(target=race) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(issued) == list(range(1, 201))
    assert current_epoch(v, "node-3") == 200


def test_stale_cas_is_a_409_never_a_silent_overwrite():
    v = FakeVariables()
    _, idx = v.get("x"); v.put("x", {"a": 1}, cas=idx)
    try:
        v.put("x", {"a": 2}, cas=idx); raise AssertionError("must conflict")
    except Conflict:
        pass


def test_one_writer_per_key():
    v = FakeVariables()
    v.acl = {"node-3": ["nodes/node-3", "nodes/node-3/*"], "node-4": ["nodes/node-4", "nodes/node-4/*"]}
    n3 = v.as_writer("node-3")
    next_epoch(n3, "node-3")
    try:
        next_epoch(n3, "node-4"); raise AssertionError("node-3 must not issue node-4's epoch")
    except Forbidden:
        pass


def test_lease_fences_when_a_newer_epoch_appears():
    v, clk = FakeVariables(), Clock()
    e, _ = next_epoch(v, "node-3")
    lease = Lease(v, "node-3", e, ttl=30, margin=5, clock=clk)
    assert lease.renew() and lease.may_write()
    next_epoch(v, "node-3")                       # a replacement was issued the next epoch
    assert lease.renew() is False and lease.fenced and lease.conflicts == 1
    assert lease.may_write() is False
    assert lease.renew() is False and lease.conflicts == 1     # fenced stays fenced; counted once


def test_lease_stops_writing_at_ttl_minus_margin_without_renewal():
    v, clk = FakeVariables(), Clock()
    e, _ = next_epoch(v, "node-3")
    lease = Lease(v, "node-3", e, ttl=30, margin=5, clock=clk)
    clk.advance(24.9); assert lease.may_write()            # 24.9 < 25
    clk.advance(0.2);  assert not lease.may_write()        # a purely local decision
    assert lease.seconds_left() == 0.0
    assert lease.renew() and lease.may_write()             # a successful renewal restores it


def test_unreachable_cluster_keeps_the_lease_only_until_the_margin():
    class Down(FakeVariables):
        def get(self, path): raise ConnectionError("partitioned")
    v, clk = FakeVariables(), Clock()
    e, _ = next_epoch(v, "node-3")
    lease = Lease(v, "node-3", e, ttl=30, margin=5, clock=clk)
    lease.vars = Down()
    clk.advance(10); assert lease.renew() is True           # still inside TTL−margin: keep recording
    clk.advance(20); assert lease.renew() is False and not lease.fenced   # expired, not fenced: nobody issued a new epoch (that we could see)
