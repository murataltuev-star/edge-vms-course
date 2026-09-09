"""The sweep that turns files back into index rows.

Two things produce segment files no index row names:

  * a fenced instance kept writing into its own epoch directory after a
    replacement took over (Lesson 4) — real footage of the partition minute;
  * a server came back after a failover with an archive whose index rows are
    on a database that was rebuilt empty elsewhere (Lesson 3: the index does
    not travel; it is rebuilt from the segments).

Both are the same sweep: walk the archive, parse <cam>/e<epoch>/<start>Z.mp4,
insert the rows that are missing, keep the epoch from the path. A segment
younger than two segment lengths may still be open and is left alone; the
console shows rows whose epoch is older than the current one as *recorded
by a fenced instance*. Nothing is deleted here; retention treats a
re-indexed segment like any other.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("cluster.reindex")

SEGMENT = re.compile(r"^(\d{8}T\d{6}Z)\.mp4$")


@dataclass
class ReindexReport:
    reindexed: int = 0
    fenced: int = 0            # of the reindexed, how many carry an epoch older than the current one
    skipped_open: int = 0
    skipped_unparseable: int = 0
    paths: list[str] = field(default_factory=list)


def parse(path: str, archive_dir: str) -> tuple[int, int, datetime] | None:
    """(camera_id, epoch, start) from <archive>/<cam>/e<epoch>/<start>Z.mp4, or None."""
    rel = os.path.relpath(path, archive_dir).split(os.sep)
    if len(rel) != 3 or not rel[0].isdigit() or not re.fullmatch(r"e\d+", rel[1]):
        return None
    m = SEGMENT.match(rel[2])
    if not m:
        return None
    start = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return int(rel[0]), int(rel[1][1:]), start


async def sweep(store, fs, archive_dir: str, current_epoch: int, segment_seconds: int,
                now: float | None = None) -> ReindexReport:
    import time
    now = now or time.time()
    report = ReindexReport()
    indexed = await store.indexed_paths_under(archive_dir)
    grace = 2 * segment_seconds
    for path, mtime in fs.walk_files(archive_dir):
        if path in indexed:
            continue
        parsed = parse(path, archive_dir)
        if parsed is None:
            report.skipped_unparseable += 1
            continue
        if now - mtime < grace:
            report.skipped_open += 1                     # may still be being written
            continue
        cam, epoch, start = parsed
        end = datetime.fromtimestamp(mtime, tz=timezone.utc)
        if end <= start:
            report.skipped_unparseable += 1
            continue
        size = fs.size(path)
        await store.index_segment(cam, start, end, path, size, epoch)
        report.reindexed += 1
        report.paths.append(path)
        if epoch < current_epoch:
            report.fenced += 1
    if report.reindexed:
        await store.log_event("archive.reindexed", None,
                              {"segments": report.reindexed, "fenced": report.fenced, "current_epoch": current_epoch})
        log.info("reindexed %d segments (%d from fenced epochs)", report.reindexed, report.fenced)
    return report


class RealFs:
    def walk_files(self, root):
        out = []
        for d, _, files in os.walk(root):
            for f in files:
                p = os.path.join(d, f)
                try:
                    out.append((p, os.path.getmtime(p)))
                except OSError:
                    pass
        return out

    def size(self, path):
        return os.path.getsize(path)
