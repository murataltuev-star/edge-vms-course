"""Lesson 3 — object first, then the Variable; the floor; `replicated`;
the six steps; unconfigured; a dangling pointer refused."""
import asyncio
from cluster.identity import Identity
from cluster.publish import Publisher
from cluster.rehydrate import RestoreRefused, rehydrate
from tests.conftest import Clock, FakeClusterStore, cam, world


async def test_publish_order_object_then_pointer():
    v, objs = world()
    store = FakeClusterStore([cam(7, revision=3)])
    calls = []
    class SpyObjs:
        def put(self, k, d): calls.append(("object", k)); objs.put(k, d)
        def get(self, k): return objs.get(k)
    class SpyVars:
        def get(self, p): return v.get(p)
        def put(self, p, items, cas=None): calls.append(("variable", items.get("config"))); return v.put(p, items, cas)
        def list(self, p): return v.list(p)
    pub = Publisher("node-3", store, SpyVars(), SpyObjs(), floor_seconds=0)
    assert await pub.publish_once()
    assert calls == [("object", "node-3/rev-3"), ("variable", "node-3/rev-3")]
    items, _ = v.get("nodes/node-3")
    assert items["config"] == "node-3/rev-3" and items["cameras"] == "7" and items["revision"] == "3"
    assert objs.get("node-3/rev-3") is not None


async def test_publish_on_change_with_a_floor():
    v, objs = world(); clk = Clock()
    store = FakeClusterStore([cam(7, revision=1)])
    pub = Publisher("node-3", store, v, objs, floor_seconds=5, clock=clk)
    await pub.publish_once(); assert pub.publishes == 1
    await pub.publish_once(); assert pub.publishes == 1        # nothing changed: nothing published
    store.edit(7, retention_days=14)                           # rev 2
    await pub.publish_once(); assert pub.publishes == 1        # inside the floor: not yet
    assert pub.replicated(2) == (False, "not yet replicated (1 edit(s) pending)")
    clk.advance(5)
    await pub.publish_once(); assert pub.publishes == 2 and pub.replicated(2) == (True, None)


async def test_directory_unreachable_never_blocks_the_edit():
    v, objs = world(); clk = Clock()
    class Down:
        def put(self, k, d): raise ConnectionError("minio down")
        def get(self, k): return None
    store = FakeClusterStore([cam(7, revision=1)])
    pub = Publisher("node-3", store, v, Down(), floor_seconds=0, clock=clk)
    assert await pub.publish_once() is False
    assert store.edit(7, name="x") == 2                        # the edit is acknowledged locally regardless
    clk.advance(90)
    ok, reason = pub.replicated(2)
    assert ok is False and "unreachable for 90s" in reason


async def test_the_six_steps_restore_a_seen_node():
    v, objs = world()
    a = FakeClusterStore([cam(7, revision=4), cam(8, revision=2)])
    await Publisher("node-3", a, v, objs, 0).publish_once()
    items, _ = v.get("nodes/node-3")
    ident = Identity("node-3", items["config"], int(items["revision"]), [7, 8], None)
    b = FakeClusterStore()                                     # step 1: empty
    r = await rehydrate(ident, b, objs)                        # steps 2–4
    assert r.state == "restored" and r.revision == 4 and r.cameras == 2
    assert b.rows[7]["revision"] == 4 and b.rows[8]["retention_days"] == 30


async def test_the_rpo_is_the_unpublished_edit():
    v, objs = world()
    a = FakeClusterStore([cam(7, revision=1)])
    pub = Publisher("node-3", a, v, objs, 0)
    await pub.publish_once()
    a.edit(7, retention_days=14)                               # acknowledged locally: rev 2
    assert pub.replicated(2)[0] is False                       # ...and shown as not yet replicated
    items, _ = v.get("nodes/node-3")
    b = FakeClusterStore()
    r = await rehydrate(Identity("node-3", items["config"], int(items["revision"]), [7], None), b, objs)
    assert r.revision == 1 and b.rows[7]["retention_days"] == 30     # the edit is gone. That is the RPO.


async def test_never_seen_comes_up_unconfigured():
    v, objs = world()
    r = await rehydrate(Identity("node-9", "", 0, [], None), FakeClusterStore(), objs)
    assert r.state == "unconfigured"


async def test_dangling_pointer_and_revision_mismatch_are_refused():
    v, objs = world()
    try:
        await rehydrate(Identity("node-3", "node-3/rev-812", 812, [7], None), FakeClusterStore(), objs)
        raise AssertionError("must refuse")
    except RestoreRefused as e:
        assert "no such object" in str(e)
    a = FakeClusterStore([cam(7, revision=4)])
    await Publisher("node-3", a, v, objs, 0).publish_once()
    try:
        await rehydrate(Identity("node-3", "node-3/rev-4", 5, [7], None), FakeClusterStore(), objs)
        raise AssertionError("must refuse")
    except RestoreRefused as e:
        assert "revision 4" in str(e)


async def test_restart_on_the_same_server_restores_nothing():
    v, objs = world()
    a = FakeClusterStore([cam(7, revision=4)])
    r = await rehydrate(Identity("node-3", "node-3/rev-1", 1, [7], None), a, objs)
    assert r.state == "already-configured" and a.rows[7]["revision"] == 4
