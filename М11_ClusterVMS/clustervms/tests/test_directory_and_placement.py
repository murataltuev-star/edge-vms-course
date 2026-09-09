"""Lesson 5 — the directory you already built, and placement with its property tests."""
import random
from cluster.directory import Directory
from cluster.placement import Camera, Node, Placer, check_invariants
from cluster.publish import Publisher
from cluster.variables import FakeVariables
from tests.conftest import Clock, FakeClusterStore, cam, world

VLANS = ["vlan:a", "vlan:b", "vlan:c"]


async def test_where_is_camera_7():
    v, objs = world(); clk = Clock()
    for n, cams in {"node-1": [1, 2, 7], "node-2": [3, 4], "node-3": [5]}.items():
        await Publisher(n, FakeClusterStore([cam(c) for c in cams]), v, objs, 0).publish_once()
    d = Directory(v, ttl=5, clock=clk)
    assert d.where(7) == "node-1" and d.where(5) == "node-3" and d.where(99) is None
    assert d.holdings("node-2") == [3, 4]
    # a move: node-1 publishes without 7, node-2 with it. Cached until the ttl, then current.
    await Publisher("node-1", FakeClusterStore([cam(1), cam(2)]), v, objs, 0).publish_once()
    await Publisher("node-2", FakeClusterStore([cam(3), cam(4), cam(7)]), v, objs, 0).publish_once()
    assert d.where(7) == "node-1"                 # cached
    clk.advance(6)
    assert d.where(7) == "node-2"                 # one raft: one answer, current


async def test_two_nodes_claiming_one_camera_is_an_error_not_a_guess():
    v, objs = world()
    await Publisher("node-1", FakeClusterStore([cam(7)]), v, objs, 0).publish_once()
    await Publisher("node-2", FakeClusterStore([cam(7)]), v, objs, 0).publish_once()
    try:
        Directory(v, ttl=0).where(7); raise AssertionError("must raise")
    except RuntimeError as e:
        assert "one-writer-per-key" in str(e)


def _world(seed, n_nodes=4, n_cams=120):
    r = random.Random(seed)
    nodes = {f"node-{i}": Node(f"node-{i}", capacity=r.choice([48, 64, 80]),
                               labels=frozenset(r.sample(VLANS, r.randint(1, 3)))) for i in range(1, n_nodes + 1)}
    cams = {}
    for c in range(1, n_cams + 1):
        cams[c] = Camera(c, r.choice([1.0, 1.0, 1.0, 2.0, 8.0]),
                         frozenset([r.choice(VLANS)]) if r.random() < 0.5 else frozenset())
    return nodes, cams


def test_every_camera_on_one_eligible_node_or_refused_and_placement_is_stored():
    for seed in range(20):
        nodes, cams = _world(seed)
        v = FakeVariables()
        p = Placer(nodes, v)
        refused = [c for c in cams.values() if p.place(c, cams) is None]
        check_invariants(p, cams)
        assert len(p.placed) + len(refused) == len(cams)
        p2 = Placer(dict(nodes), v)                # a fresh process reads the SAME placement back
        assert {c: pl.node for c, pl in p2.placed.items()} == {c: pl.node for c, pl in p.placed.items()}


def test_adding_a_node_moves_nothing():
    for seed in range(20):
        nodes, cams = _world(seed)
        p = Placer(nodes, FakeVariables())
        for c in cams.values():
            p.place(c, cams)
        before = {c: pl.node for c, pl in p.placed.items()}
        p.add_node(Node("node-9", capacity=80, labels=frozenset(VLANS)))
        assert {c: pl.node for c, pl in p.placed.items()} == before


def test_tidy_rebalance_fails_the_stability_rule():
    nodes, cams = _world(7)
    p = Placer(nodes, FakeVariables())
    for c in cams.values():
        p.place(c, cams)
    before = {c: pl.node for c, pl in p.placed.items()}
    p.placed.clear()                                          # "re-place everything optimally, big first"
    for c in sorted(cams.values(), key=lambda c: -c.load):
        p.place(c, cams)
    check_invariants(p, cams)                                 # every invariant holds...
    moved = sum(1 for c in before if c in p.placed and p.placed[c].node != before[c])
    assert moved > 0                                          # ...and cameras moved for no reason


def test_budgeted_rebalance_has_a_reason_and_a_revision():
    nodes, cams = _world(3)
    p = Placer(nodes, FakeVariables())
    for c in cams.values():
        p.place(c, cams)
    p.retire_node("node-2", cams)
    p.add_node(Node("node-2", capacity=64, labels=frozenset(VLANS)))
    moves = p.rebalance(cams, budget=5)
    assert 0 < len(moves) <= 5
    check_invariants(p, cams)
    for c, frm, to in moves:
        assert p.placed[c].node == to and "rebalance" in p.placed[c].reason and p.placed[c].rev > 0
    assert p.rebalance(cams, budget=0) == []                  # interruptible: no budget, no moves


def test_capacity_is_the_system_not_a_node():
    p = Placer({"node-1": Node("node-1", 2.0), "node-2": Node("node-2", 2.0)}, FakeVariables())
    cams = {i: Camera(i, 1.0) for i in range(1, 6)}
    results = [p.place(c, cams) for c in cams.values()]
    assert all(results[:4]) and results[4] is None
