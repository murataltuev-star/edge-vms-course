"""The whole thing with fakes: prologue, the zombie fenced, the two numbers."""
import asyncio
from cluster.apphost import ClusterAppHost
from cluster.identity import Identity
from cluster.metrics import render
from cluster.publish import Publisher
from apphost.config import Settings
from apphost.pipeline import FakeActuator
from tests.conftest import Clock, FakeClusterStore, cam, world
import os


def settings(**kw):
    env = {"ARCHIVE_DIR": "/tmp/clustervms-test-archive", "DATABASE_URL": "postgresql://none"}
    env.update({k.upper(): str(v) for k, v in kw.items()})
    old = dict(os.environ); os.environ.update(env)
    try:
        return Settings()
    finally:
        os.environ.clear(); os.environ.update(old)


async def _seen_node(v, objs, cams):
    a = FakeClusterStore([cam(c, revision=2) for c in cams])
    await Publisher("node-3", a, v, objs, 0).publish_once()
    items, _ = v.get("nodes/node-3")
    return Identity("node-3", items["config"], int(items["revision"]), cams, None)


async def test_prologue_restores_and_records_into_a_new_epoch():
    v, objs = world(); clk = Clock(); wall = Clock(10_000.0)
    ident = await _seen_node(v, objs, [7, 8])
    v.put("nodes/node-3/heartbeat", {"ts": "9950.0", "epoch": "1"})       # the old instance's last sign of life
    b = FakeClusterStore()
    host = ClusterAppHost(settings(), b, v, objs, ident, actuator=FakeActuator(), clock=clk, wall=wall)
    r = await host.prologue()
    assert r.state == "restored" and r.cameras == 2
    assert host.settings.epoch == 1 and host.actuator.settings.epoch == 1
    assert await host.reconcile_once() == [("start", 7), ("start", 8)]
    assert host.failover == {"last": 50.0, "worst": 50.0}                 # node_failover_seconds
    fo, _ = v.get("nodes/node-3/failover"); assert fo["worst"] == "50.0"
    await host.report_once()
    assert b.conditions[(7, "replicated")] == (True, None)
    assert "node_epoch_conflicts{node=\"node-3\"} 0" in render(host)


async def test_zombie_is_fenced_when_the_replacement_takes_the_epoch():
    v, objs = world(); clk = Clock()
    ident = await _seen_node(v, objs, [7])
    act_a, act_b = FakeActuator(), FakeActuator()
    a = ClusterAppHost(settings(), FakeClusterStore(), v, objs, ident, actuator=act_a, clock=clk)
    await a.prologue(); await a.reconcile_once()
    assert act_a.running == {7} and a.settings.epoch == 1
    # Nomad thinks A is lost; B starts on another server and takes epoch 2
    b = ClusterAppHost(settings(), FakeClusterStore(), v, objs, ident, actuator=act_b, clock=clk)
    await b.prologue(); await b.reconcile_once()
    assert b.settings.epoch == 2 and act_b.running == {7}
    # A wakes (SIGCONT) and renews its lease: the epoch moved. It fences itself.
    assert a.lease.renew() is False
    a.fence("a newer epoch was issued")
    assert act_a.running == set() and a.recording_allowed is False
    assert await a.reconcile_once() == [("failed", 7)]                   # it may start nothing
    assert "node_epoch_conflicts{node=\"node-3\"} 1" in render(a)          # the counter moved
    assert "node_epoch_conflicts{node=\"node-3\"} 0" in render(b)
    # B's lease is fine, and its archive path carries its own epoch
    assert b.lease.renew() and b.actuator.settings.epoch == 2


async def test_unseen_node_records_nothing_and_says_so():
    v, objs = world()
    host = ClusterAppHost(settings(), FakeClusterStore(), v, objs, Identity("node-9", "", 0, [], None),
                          actuator=FakeActuator())
    r = await host.prologue()
    assert r.state == "unconfigured" and await host.reconcile_once() == []
    assert host.settings.epoch == 1                                       # it still holds an epoch: an operator may configure it
