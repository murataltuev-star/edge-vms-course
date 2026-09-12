"""Lesson 3, second half — events. Written by the observer beside the
segment; served by the resource; indexed by a job that is a cache and
proves it by being deleted and rebuilt; unavailable — by name — when the
resource is, never lost."""
import json
from cluster.eventindex import EventIndex
from cluster.resource import ResourceHeartbeat, resources_seen
from vms.archive import Manifest, append_event, read_events, segment_path
from datetime import datetime, timezone
import os
from tests.conftest import Cluster


class DirReader:
    def __init__(self, c): self.c = c
    def _srv(self, url):
        s = self.c.servers[url.rsplit("/", 1)[1]]
        if getattr(s, "down", False): raise ConnectionError(s.name)
        return s
    def manifest(self, url, cam): return Manifest(self._srv(url).archive, cam).read()
    def events(self, url, seg): return read_events(self._srv(url).archive, seg)


def _record(c, server, cam, epoch, start, events):
    """A worker wrote a segment and observed some things while doing it; the resource promoted both."""
    srv = c.servers[server]
    p = segment_path(srv.spool, cam, epoch, datetime.fromtimestamp(start, timezone.utc))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(b"x" * 1000)
    for dt, kind, fields in events:
        append_event(p, start + dt, kind, **fields)
    os.utime(p, (start + 600, start + 600))
    return srv.resource.promote(p)


def test_events_are_indexed_across_resources_and_the_index_is_a_cache():
    c = Cluster(); t = c.wall() - 3600
    _record(c, "srv-a", 7, 3, t,       [(12, "motion", {"zone": "gate"}), (40, "person", {"score": 0.9})])
    _record(c, "srv-a", 7, 3, t + 600, [])
    _record(c, "srv-b", 7, 4, t + 1200, [(5, "person", {"score": 0.7})])          # after a failover: next epoch, other server
    _record(c, "srv-c", 9, 1, t,       [(1, "motion", {})])
    hbs = {s: ResourceHeartbeat(srv.resource, c.objects, s, f"http://{s}", wall=c.wall) for s, srv in c.servers.items()}
    for hb in hbs.values(): hb.once()
    idx = EventIndex(DirReader(c), wall=c.wall)
    rep = idx.rebuild(resources_seen(c.objects))
    assert rep == {"added": 4, "unreachable": [], "segments": 3} and idx.state == "live"   # the empty segment was skipped
    q = idx.query(t, t + 3600, cam=7, current_epochs={7: 4})
    assert [(e["kind"], e["server"], e["epoch"], e["fenced"]) for e in q["events"]] == \
           [("motion", "srv-a", 3, True), ("person", "srv-a", 3, True), ("person", "srv-b", 4, False)]
    assert q["events"][0]["zone"] == "gate" and q["events"][0]["segment"].startswith("7/e3/")
    assert [e["cam"] for e in idx.query(t, t + 3600, kind="person")["events"]] == [7, 7]
    # the index is a cache: a new instance after a failover rebuilds to the same answer from the resources alone
    idx2 = EventIndex(DirReader(c), wall=c.wall); idx2.rebuild(resources_seen(c.objects))
    assert idx2.query(t, t + 3600)["events"] == idx.query(t, t + 3600)["events"]
    # tail: a new segment with events on srv-c
    _record(c, "srv-c", 9, 1, t + 600, [(3, "motion", {})]); hbs["srv-c"].once()
    assert idx.tail(resources_seen(c.objects))["added"] == 1


def test_a_dead_resource_makes_the_answer_incomplete_by_name_not_wrong():
    c = Cluster(); t = c.wall() - 3600
    _record(c, "srv-a", 7, 3, t, [(12, "motion", {})])
    _record(c, "srv-b", 8, 1, t, [(20, "motion", {})])
    hbs = {s: ResourceHeartbeat(srv.resource, c.objects, s, f"http://{s}", wall=c.wall) for s, srv in c.servers.items()}
    for hb in hbs.values(): hb.once()
    c.wall.advance(60); hbs["srv-b"].once(); hbs["srv-c"].once()                  # srv-a went silent
    idx = EventIndex(DirReader(c), wall=c.wall)                                     # a fresh eventindex, after a failover of its own
    rep = idx.rebuild(resources_seen(c.objects))
    assert rep["added"] == 1 and rep["unreachable"] == ["srv-a"] and idx.state == "live; srv-a unreachable"
    assert [e["cam"] for e in idx.query(t, t + 3600)["events"]] == [8]              # srv-a's events are unavailable, and the state says so
    hbs["srv-a"].once(); c.servers["srv-a"].down = False
    assert idx.tail(resources_seen(c.objects))["added"] == 1 and idx.state == "live"   # back with its disks: indexed, not rebuilt
    # retention on srv-b removed its segment: the index forgets, by (server, path)
    seg = Manifest(c.servers["srv-b"].archive, 8).read()[0]
    assert idx.forget("srv-b", [seg.path]) == 1 and [e["cam"] for e in idx.query(t, t + 3600)["events"]] == [7]
    assert c.vars.list("vms/events") == [] and c.objects.list("vms/events") == []   # no controller, no database, wrote an event
