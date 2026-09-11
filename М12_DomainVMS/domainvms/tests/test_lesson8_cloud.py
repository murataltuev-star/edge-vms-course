"""Lesson 8 — the bandwidth arithmetic before the demo, and a Node that
cannot tell where it runs."""
from domain.cloud import node_stanza, recommend, render_three_ways, storage_tb, tb_per_day


def test_fifty_cameras_at_four_megabit():
    assert abs(tb_per_day(50, 4.0) - 2.16) < 0.01                    # ~2 TB a day
    r = recommend(50, 4.0, uplink_mbit=100)
    assert r.shape == "mixed" and r.upstream_mbit == 200 and "recording stays at the edge" in r.reason
    assert abs(storage_tb(6, 4.0, 30) - 7.8) < 0.1                   # six cameras, thirty days: a few TB
    small = recommend(6, 4.0, uplink_mbit=1000)
    assert small.shape == "cloud" and "not cheaper" in small.reason
    big = recommend(50, 4.0, uplink_mbit=10_000)
    assert big.shape == "edge" and "loses money per camera" in big.reason


def test_a_node_deployed_three_ways_is_the_same_artifact():
    jobs = render_three_ways("node-3")
    assert jobs["local"] != jobs["rented"]                            # the datacenter and the object store differ...
    assert node_stanza(jobs["local"]) == node_stanza(jobs["rented"]) == node_stanza(jobs["split"])   # ...and nothing about the Node does
    assert 'lost_after           = "45s"' in jobs["rented"] and "EPOCH" not in jobs["rented"]
