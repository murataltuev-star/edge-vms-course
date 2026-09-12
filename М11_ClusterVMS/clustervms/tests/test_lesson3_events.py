"""Lesson 3, second half — events. Written by the worker holding a unit's
epoch into the unit's bucket on its server's resource; any subsystem, its
own prefix; indexed by a job that is a cache and proves it by being
deleted and rebuilt; unavailable — by name — when the resource is, never
lost; a detector's event about camera 7 found by a field, not by living in
camera 7's bucket."""
import os
from datetime import datetime, timezone
from cluster.eventindex import EventIndex
from cluster.resource import ResourceHeartbeat, ResourcePolicy, resources_seen
from vms.archive import Manifest, event_log, segment_path
from vmsplatform.events import EventLog, buckets_under, read_bucket
from tests.conftest import Cluster

B = 600


class DirReader:
    def __init__(self, c): self.c = c
    def _srv(self, url):
        s = self.c.servers[url.rsplit("/", 1)[1]]
        if getattr(s, "down", False): raise ConnectionError(s.name)
        return s
    def buckets(self, url, sub, unit): return buckets_under(self._srv(url).archive, sub, unit, B)
    def events(self, url, b): return read_bucket(os.path.join(self._srv(url).archive, b.path))


def _media(c, server, cam, epoch, start):
    srv = c.servers[server]
    p = segment_path(srv.spool, cam, epoch, datetime.fromtimestamp(start, timezone.utc))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(b"x" * 1000)
    os.utime(p, (start + B, start + B))
    return srv.resource.promote(p)


def _observe(c, server, sub, unit, epoch, t, kind, **fields):
    """A worker of `sub` holding `unit`'s epoch on `server` observed something."""
    return EventLog(c.servers[server].archive, sub, unit, epoch, B).append(t, kind, **fields)


def _heartbeats(c):
    hbs = {s: ResourceHeartbeat(srv.resource, c.objects, s, f"http://{s}", wall=c.wall) for s, srv in c.servers.items()}
    for hb in hbs.values(): hb.once()
    return hbs


def test_events_are_indexed_across_resources_and_subsystems_and_the_index_is_a_cache():
    c = Cluster(); t = c.wall() - 3600
    _media(c, "srv-a", 7, 3, t)
    _observe(c, "srv-a", "vms", "7", 3, t + 12, "motion", zone="gate")            # the VMS worker, recording camera 7
    _observe(c, "srv-a", "vms", "7", 3, t + 40, "silent")
    _observe(c, "srv-b", "vms", "7", 4, t + 1205, "motion")                        # after a failover: next epoch, other server, not recording
    _observe(c, "srv-c", "det", "d-12", 1, t + 30, "person", cam=7, score=0.9)   # a detector on a GPU server, ABOUT camera 7
    _observe(c, "srv-c", "counter", "a", 1, t + 5, "round", value=10)             # a third subsystem, its own prefix
    hbs = _heartbeats(c)
    assert resources_seen(c.objects)["srv-c"]["units"] == {"counter": ["a"], "det": ["d-12"]}
    idx = EventIndex(DirReader(c), wall=c.wall)
    rep = idx.rebuild(resources_seen(c.objects))
    assert rep == {"added": 5, "unreachable": [], "segments": 4} and idx.state == "live"
    q = idx.query(t, t + 3600, cam=7, current_epochs={("vms", "7"): 4})
    assert [(e["subsystem"], e["unit"], e["kind"], e["server"], e["epoch"], e["fenced"]) for e in q["events"]] == [
        ("vms", "7", "motion", "srv-a", 3, True), ("det", "d-12", "person", "srv-c", 1, False),
        ("vms", "7", "silent", "srv-a", 3, True), ("vms", "7", "motion", "srv-b", 4, False)]
    assert q["events"][1]["score"] == 0.9 and q["events"][1]["bucket"].startswith("det/d-12/e1/")   # found by the field; it lives in ITS bucket
    assert [e["value"] for e in idx.query(t, t + 3600, subsystem="counter")["events"]] == [10]
    assert [e["cam"] for e in idx.query(t, t + 3600, kind="motion")["events"]] == [7, 7]
    # the index is a cache: a new instance after a failover rebuilds to the same answer from the resources alone
    idx2 = EventIndex(DirReader(c), wall=c.wall); idx2.rebuild(resources_seen(c.objects))
    assert idx2.query(t, t + 3600)["events"] == idx.query(t, t + 3600)["events"]
    # tail: a new bucket on srv-c
    _observe(c, "srv-c", "det", "d-12", 1, t + 700, "person", cam=9); hbs["srv-c"].once()
    assert idx.tail(resources_seen(c.objects))["added"] == 1


def test_a_dead_resource_makes_the_answer_incomplete_by_name_not_wrong():
    c = Cluster(); t = c.wall() - 3600
    _observe(c, "srv-a", "vms", "7", 3, t + 12, "motion")
    _observe(c, "srv-b", "vms", "8", 1, t + 20, "motion")
    hbs = _heartbeats(c)
    c.wall.advance(60); hbs["srv-b"].once(); hbs["srv-c"].once()                  # srv-a went silent
    idx = EventIndex(DirReader(c), wall=c.wall)                                     # a fresh eventindex, after a failover of its own
    rep = idx.rebuild(resources_seen(c.objects))
    assert rep["added"] == 1 and rep["unreachable"] == ["srv-a"] and idx.state == "live; srv-a unreachable"
    assert [e["cam"] for e in idx.query(t, t + 3600)["events"]] == [8]              # srv-a's events are unavailable, and the state says so
    hbs["srv-a"].once()
    assert idx.tail(resources_seen(c.objects))["added"] == 1 and idx.state == "live"   # back with its disks: indexed, not rebuilt
    # retention on srv-b removed a bucket: the index forgets, by (server, path)
    b = buckets_under(c.servers["srv-b"].archive, "vms", "8", B)[0]
    assert idx.forget("srv-b", [b.path]) == 1 and [e["cam"] for e in idx.query(t, t + 3600)["events"]] == [7]
    assert c.vars.list("vms/events") == [] and c.objects.list("vms/events") == []   # no controller, no database, wrote an event


def test_the_resource_policy_closes_buckets_and_retains_per_subsystem():
    from cluster.controller import ClusterController
    c = Cluster(); ctl = ClusterController(c.vars, c.objects, wall=c.wall); srv = c.servers["srv-a"]
    ctl.create_camera({"source": "driverpack://file/7.mp4", "retention_days": 1, "events_retention_days": 30})
    now = c.wall()
    p1 = _observe(c, "srv-a", "vms", "1", 1, now - 40 * 86400, "motion")       # older than the events policy
    p2 = _observe(c, "srv-a", "vms", "1", 1, now - 3600, "motion")             # recent
    p3 = _observe(c, "srv-a", "det", "d-1", 1, now - 400 * 86400, "person")   # another subsystem: a year by default
    for p in (p1, p2, p3): os.utime(p, (now - 100, now - 100))
    rep = ResourcePolicy(srv.resource, c.vars, wall=c.wall).once()
    assert rep["added"] == 2 and rep["closed"] == 0 and rep["removed"] == 2 and os.path.exists(p2)   # repair indexed them first; close had nothing left and not os.path.exists(p1) and not os.path.exists(p3)
    assert len(Manifest(srv.archive, 1).buckets()) == 1
