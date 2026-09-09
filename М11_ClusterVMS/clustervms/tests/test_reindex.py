"""Lesson 4 — a fenced instance's footage is re-indexed with its epoch, never deleted."""
import os, tempfile, time
from datetime import datetime, timezone
from cluster.reindex import parse, sweep
from tests.conftest import FakeClusterStore


class Fs:
    def __init__(self): self.files = {}
    def put(self, path, size, mtime): self.files[path] = (size, mtime)
    def walk_files(self, root): return [(p, m) for p, (_, m) in self.files.items() if p.startswith(root)]
    def size(self, path): return self.files[path][0]


ARCH = "/data/archive"


def seg(fs, cam, epoch, start_iso, seconds, size=1000):
    start = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    p = f"{ARCH}/{cam}/e{epoch}/{start:%Y%m%dT%H%M%SZ}.mp4"
    fs.put(p, size, start.timestamp() + seconds)
    return p


def test_parse():
    assert parse(f"{ARCH}/7/e5/20260908T091000Z.mp4", ARCH) == (7, 5, datetime(2026, 9, 8, 9, 10, tzinfo=timezone.utc))
    assert parse(f"{ARCH}/7/e5/index.json", ARCH) is None
    assert parse(f"{ARCH}/7/20260908T091000Z.mp4", ARCH) is None


async def test_fenced_segments_come_back_with_their_epoch():
    store, fs = FakeClusterStore(), Fs()
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc).timestamp()
    live = seg(fs, 7, 6, "2026-09-08T10:00:00", 600)          # the replacement's, already indexed
    await store.index_segment(7, datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
                              datetime(2026, 9, 8, 10, 10, tzinfo=timezone.utc), live, 1000, 6)
    z1 = seg(fs, 7, 5, "2026-09-08T10:00:00", 600)            # the zombie's, after the fence
    z2 = seg(fs, 7, 5, "2026-09-08T10:10:00", 600)
    still_open = seg(fs, 7, 6, "2026-09-08T11:55:00", 240)    # closed 1 minute ago: leave it
    junk = f"{ARCH}/7/e6/index.json"; fs.put(junk, 10, now - 9999)
    rep = await sweep(store, fs, ARCH, current_epoch=6, segment_seconds=600, now=now)
    assert rep.reindexed == 2 and rep.fenced == 2 and rep.skipped_open == 1 and rep.skipped_unparseable == 1
    rows = {r[3]: r for r in store.segments}
    assert rows[z1][5] == 5 and rows[z2][5] == 5                 # epoch kept from the path
    assert rows[z1][1] == datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    assert rows[z1][2] == datetime(2026, 9, 8, 10, 10, tzinfo=timezone.utc)
    assert any(k == "archive.reindexed" and p["fenced"] == 2 for k, _, p in store.events)
    # idempotent: a second sweep finds nothing new
    rep2 = await sweep(store, fs, ARCH, current_epoch=6, segment_seconds=600, now=now)
    assert rep2.reindexed == 0


async def test_returned_server_archive_is_rebuilt_from_segments():
    """Lesson 3: the index did not travel. When the dead server's disks come
    back, the same sweep rebuilds it — nothing fenced about these, epoch == current."""
    store, fs = FakeClusterStore(), Fs()
    now = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    for h in range(0, 6):
        seg(fs, 7, 6, f"2026-09-08T{h:02d}:00:00", 600)
    rep = await sweep(store, fs, ARCH, current_epoch=6, segment_seconds=600, now=now)
    assert rep.reindexed == 6 and rep.fenced == 0
