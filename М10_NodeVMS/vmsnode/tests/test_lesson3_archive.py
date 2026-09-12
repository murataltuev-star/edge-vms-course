"""Lesson 3 — the archive as a resource: promote, manifest, repair, retention."""
import os
from datetime import datetime, timezone
from vms.archive import ArchiveResource, Manifest, parse, segment_path
from tests.conftest import Box


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def write_segment(root, cam, epoch, start, size=1000, mtime=None):
    p = segment_path(root, cam, epoch, utc(start))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\0" * size)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_parse_and_paths():
    assert parse("/a/7/e5/20260912T101000Z.mp4", "/a") == (7, 5, utc("2026-09-12T10:10:00"))
    assert parse("/a/7/e5/manifest.jsonl", "/a") is None and parse("/a/7/20260912T101000Z.mp4", "/a") is None


def test_promote_is_the_acknowledgement_order():
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    p = write_segment(box.spool, 7, 3, "2026-09-12T10:00:00", mtime=utc("2026-09-12T10:10:00").timestamp())
    seg = res.promote(p)
    assert not os.path.exists(p)                                              # 3. gone from the spool, last
    assert os.path.exists(os.path.join(box.archive, seg.path))                # 1. in the archive, whole
    lines = Manifest(box.archive, 7).read()                                    # 2. named in the manifest
    assert len(lines) == 1 and lines[0].epoch == 3 and lines[0].end - lines[0].start == 600 and lines[0].bytes == 1000


def test_kill_mid_segment_open_lost_closed_kept():
    """Seven minutes of a ten-minute segment length: six closed segments
    promoted (or still in the spool, closed), one open segment lost."""
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    now = utc("2026-09-12T12:00:00").timestamp()
    for m in range(0, 60, 10):                                                 # 6 closed, promoted in time
        p = write_segment(box.spool, 7, 3, f"2026-09-12T11:{m:02d}:00", mtime=now - 3600 + (m + 10) * 60)
        res.promote(p)
    late = write_segment(box.spool, 7, 3, "2026-09-12T12:00:00", mtime=now - 60)        # closed, worker died before promote
    write_segment(box.spool, 7, 3, "2026-09-12T12:10:00", size=10, mtime=now - 5)      # the open one
    assert res.closed_in_spool(grace_seconds=30, now=now) == [late]           # what the restart promotes
    res.promote(late)
    assert len(Manifest(box.archive, 7).read()) == 7
    # the open segment is what a kill loses: up to one segment length, the number М9 Lesson 4 stated
    assert res.closed_in_spool(grace_seconds=30, now=now) == []


def test_manifest_rebuilt_from_the_files_alone():
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    for m in ("10:00:00", "10:10:00", "10:20:00"):
        res.promote(write_segment(box.spool, 7, 3, f"2026-09-12T{m}"))
    orig = Manifest(box.archive, 7).read()
    os.remove(Manifest(box.archive, 7).path)                                   # the index did not travel
    rep = res.repair()
    assert rep == {"added": 3, "dropped": 0}
    rebuilt = Manifest(box.archive, 7).read()
    assert [(s.path, s.epoch, s.start) for s in rebuilt] == [(s.path, s.epoch, s.start) for s in orig]
    os.remove(os.path.join(box.archive, orig[0].path))                        # a file went missing under a line
    assert res.repair() == {"added": 0, "dropped": 1} and len(Manifest(box.archive, 7).read()) == 2
    assert res.repair() == {"added": 0, "dropped": 0}                          # idempotent


def test_timeline_marks_a_fenced_epoch_and_spans_two_resources():
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    res.promote(write_segment(box.spool, 7, 3, "2026-09-12T10:00:00", mtime=utc("2026-09-12T10:10:00").timestamp()))
    res.promote(write_segment(box.spool, 7, 4, "2026-09-12T10:10:00", mtime=utc("2026-09-12T10:20:00").timestamp()))
    res.promote(write_segment(box.spool, 7, 3, "2026-09-12T10:10:00", mtime=utc("2026-09-12T10:15:00").timestamp()))  # the zombie's
    tl = Manifest(box.archive, 7).timeline(utc("2026-09-12T10:05:00").timestamp(), utc("2026-09-12T10:30:00").timestamp(), current_epoch=4)
    assert [(t["epoch"], t["fenced"]) for t in tl] == [(3, True), (3, True), (4, False)]
    # a second resource (another server) holds later footage: the console merges two manifests
    other = ArchiveResource(box.spool + "2", box.archive + "2")
    other.promote(write_segment(other.spool, 7, 5, "2026-09-12T10:20:00", mtime=utc("2026-09-12T10:30:00").timestamp()))
    merged = sorted(Manifest(box.archive, 7).timeline(0, 1e12) + Manifest(other.root, 7).timeline(0, 1e12), key=lambda t: t["start"])
    assert [t["epoch"] for t in merged] == [3, 3, 4, 5]


def test_retention_is_a_policy_on_the_resource():
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    now = utc("2026-10-20T00:00:00").timestamp()
    for day in (1, 10, 19):
        res.promote(write_segment(box.spool, 7, 3, f"2026-10-{day:02d}T10:00:00", mtime=utc(f"2026-10-{day:02d}T10:10:00").timestamp()))
    assert res.retain(7, days=8, now=now) == 2                              # cutoff 12 Oct: the 1st and the 10th go
    left = Manifest(box.archive, 7).read()
    assert len(left) == 1 and not os.path.exists(os.path.join(box.archive, "7", "e3", "20261001T100000Z.mp4"))
    assert res.usage() == 1000


def test_events_ride_with_the_segment():
    """An event is an observation, written by the observer under its epoch,
    beside what it describes. It is promoted with the segment, counted in the
    manifest line, shown on the timeline, fenced by the same path, and
    deleted by the same retention. No controller wrote it; no database holds it."""
    from vms.archive import append_event, events_path, read_events
    box = Box(); res = ArchiveResource(box.spool, box.archive)
    t0 = utc("2026-09-12T10:00:00").timestamp()
    p = write_segment(box.spool, 7, 3, "2026-09-12T10:00:00", mtime=t0 + 600)
    append_event(p, t0 + 12.5, "motion", zone="gate")                          # the worker, while the segment is open
    append_event(p, t0 + 40.0, "person", score=0.91)
    seg = res.promote(p)
    assert seg.events == 2 and not os.path.exists(events_path(p))              # promoted with it: gone from the spool
    assert read_events(box.archive, seg) == [{"t": t0 + 12.5, "kind": "motion", "zone": "gate"},
                                             {"t": t0 + 40.0, "kind": "person", "score": 0.91}]
    assert Manifest(box.archive, 7).timeline(t0, t0 + 600)[0]["events"] == 2  # the timeline says how many without opening it
    os.remove(os.path.join(box.archive, "7", "manifest.jsonl"))
    assert res.repair() == {"added": 1, "dropped": 0} and Manifest(box.archive, 7).read()[0].events == 2   # rebuilt from the files
    assert res.retain(7, days=1, now=t0 + 3 * 86400) == 1
    assert not os.path.exists(events_path(os.path.join(box.archive, seg.path)))   # retention takes both
