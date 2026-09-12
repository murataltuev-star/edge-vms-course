"""The archive as a resource — server-bound, no controller, a policy.

    <spool>/vms/<cam>/e<epoch>/<start>Z.mp4        the open segment, and closed ones not yet promoted
    <archive>/vms/<cam>/e<epoch>/<start>Z.mp4      promoted: the resource's media
    <archive>/vms/<cam>/e<epoch>/<start>Z.events.jsonl
                                                   the camera's EVENT BUCKETS — the platform's event log
                                                   (vmsplatform.events), written by the worker holding the
                                                   camera's epoch, whether or not it is recording
    <archive>/vms/<cam>/manifest.jsonl             one line per media segment and one per closed event bucket:
                                                   the index that lives beside the footage

The archive's unit is a TIME SPAN under an epoch, not a media file. A span
may hold media (archivesink wrote it), events (the worker observed
something — motion, silence, an operator's mark), or both. A camera that
is watched and never recorded still has buckets. A camera that went silent
has no segment open — and the event that says so goes into its bucket.

The acknowledgement order is М9 Lesson 4's: a closed segment is PROMOTED
(renamed into the archive, then a manifest line appended), and the spool
copy is gone only after that. An event bucket is written in place on the
resource, one flushed line at a time, and gets its manifest line when it
closes (its span is over and nothing has touched it for a grace period).
The manifest is append-only and rebuildable from the files.

Retention is a policy, per kind. Media is the VMS's: `retain()` after
`retention_days`, files first then lines. Buckets are the PLATFORM's
(vmsplatform.resource): the controller writes `vms/retention/<cam>
{days: events_retention_days}` and the resource job deletes the files —
events are small and often kept a year where footage is kept a month —
and `repair()` drops the lines whose files are gone.

The layout is `<subsystem>/<unit>/...` — the platform resource's tree — so
that other subsystems' buckets sit on the same server under their own
prefix. `ArchivePolicy` is what the VMS registers with the platform's
resource job: its own pass over its own part of the tree.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone

from vmsplatform.events import Bucket, EventLog, buckets_under, read_bucket, unit_dir

SUB = "vms"
SEGMENT = re.compile(r"^(\d{8}T\d{6}Z)\.mp4$")
EPOCH_DIR = re.compile(r"^e(\d+)$")


def segment_path(root: str, cam: int, epoch: int, start: datetime) -> str:
    return os.path.join(root, SUB, str(cam), f"e{epoch}", start.strftime("%Y%m%dT%H%M%SZ") + ".mp4")


def parse(path: str, root: str) -> tuple[int, int, datetime] | None:
    rel = os.path.relpath(path, root).split(os.sep)
    if len(rel) != 4 or rel[0] != SUB or not rel[1].isdigit() or not EPOCH_DIR.match(rel[2]):
        return None
    m = SEGMENT.match(rel[3])
    if not m:
        return None
    return int(rel[1]), int(rel[2][1:]), datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def event_log(root: str, cam: int, epoch: int, bucket_seconds: int = 600) -> EventLog:
    """The camera's event log on this resource: what the worker holding the
    camera's epoch writes into, recording or not."""
    return EventLog(root, SUB, str(cam), epoch, bucket_seconds)


@dataclass(frozen=True)
class Segment:
    cam: int
    epoch: int
    start: float          # unix seconds
    end: float
    path: str             # relative to the archive root
    bytes: int

    def line(self) -> str:
        return json.dumps({"kind": "media", "cam": self.cam, "epoch": self.epoch, "start": self.start, "end": self.end,
                           "path": self.path, "bytes": self.bytes})

    @classmethod
    def from_line(cls, line: str) -> "Segment":
        d = json.loads(line)
        return cls(int(d["cam"]), int(d["epoch"]), float(d["start"]), float(d["end"]), d["path"], int(d["bytes"]))


def bucket_from_line(line: str) -> Bucket:
    d = json.loads(line)
    return Bucket(d["subsystem"], str(d["unit"]), int(d["epoch"]), float(d["start"]), float(d["end"]), d["path"], int(d["events"]))


class Manifest:
    """Per camera, append-only, beside the footage."""

    def __init__(self, archive_root: str, cam: int):
        self.path = os.path.join(unit_dir(archive_root, SUB, str(cam)), "manifest.jsonl")

    def append(self, entry) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a") as f:
            f.write(entry.line() + "\n")

    def _lines(self) -> list[str]:
        try:
            with open(self.path) as f:
                return [l for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def read(self) -> list[Segment]:
        """The media lines — what a player needs."""
        return [Segment.from_line(l) for l in self._lines() if json.loads(l).get("kind", "media") == "media"]

    def buckets(self) -> list[Bucket]:
        """The closed event buckets — what an index needs."""
        return [bucket_from_line(l) for l in self._lines() if json.loads(l).get("kind") == "events"]

    def rewrite(self, segs: list[Segment], buckets: list[Bucket] | None = None) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for e in sorted(list(segs) + list(buckets if buckets is not None else self.buckets()), key=lambda e: (e.start, e.epoch)):
                f.write(e.line() + "\n")
        os.replace(tmp, self.path)

    def timeline(self, t0: float, t1: float, current_epoch: int | None = None) -> list[dict]:
        """Spans overlapping [t0, t1): media segments, and event buckets with no
        media (the camera was watched, not recorded). Each marked *fenced* if its
        epoch is older than the current."""
        out = []
        for s in self.read():
            if s.end > t0 and s.start < t1:
                out.append({"start": s.start, "end": s.end, "media": s.path, "epoch": s.epoch, "events": 0,
                            "fenced": current_epoch is not None and s.epoch < current_epoch})
        for b in self.buckets():
            if b.end > t0 and b.start < t1:
                hit = next((o for o in out if o["media"] and o["epoch"] == b.epoch and o["start"] < b.end and b.start < o["end"]), None)
                if hit:
                    hit["events"] += b.events               # events during a recorded span: count them on it
                else:
                    out.append({"start": b.start, "end": b.end, "media": None, "epoch": b.epoch, "events": b.events,
                                "fenced": current_epoch is not None and b.epoch < current_epoch})
        return sorted(out, key=lambda d: (d["start"], d["epoch"]))


class ArchiveResource:
    """One server's archive. `promote()` is what archivesink calls on
    fragment-closed; `repair()` is what М11 called the re-index sweep."""

    def __init__(self, spool_root: str, archive_root: str, bucket_seconds: int = 600, wall=None):
        import time
        self.spool, self.root, self.bucket_seconds = spool_root, archive_root, bucket_seconds
        self.wall = wall or time.time
        os.makedirs(self.spool, exist_ok=True)
        os.makedirs(self.root, exist_ok=True)

    def promote(self, spool_path: str, end: float | None = None) -> Segment:
        parsed = parse(spool_path, self.spool)
        if parsed is None:
            raise ValueError(f"not a segment path: {spool_path}")
        cam, epoch, start = parsed
        st = os.stat(spool_path)
        end = end if end is not None else st.st_mtime
        rel = os.path.relpath(spool_path, self.spool)
        dest = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._move(spool_path, dest)                    # 1. into the archive, atomically (same filesystem)
        seg = Segment(cam, epoch, start.timestamp(), end, rel, st.st_size)
        Manifest(self.root, cam).append(seg)            # 2. then the line
        return seg

    def close_buckets(self, now: float, grace_seconds: float = 30.0, bucket_seconds: int = 600) -> list[Bucket]:
        """Event buckets whose span is over and that nobody has written to for
        the grace get their manifest line. The events were durable the moment
        they were written; this is the index catching up, not an acknowledgement."""
        closed = []
        for cam in self.cameras():
            man = Manifest(self.root, cam)
            known = {b.path for b in man.buckets()}
            for b in buckets_under(self.root, SUB, str(cam), bucket_seconds):
                p = os.path.join(self.root, b.path)
                if b.path in known or b.end > now or now - os.path.getmtime(p) < grace_seconds:
                    continue
                man.append(b); closed.append(b)
        return closed

    def cameras(self) -> list[int]:
        try:
            return sorted(int(d) for d in os.listdir(os.path.join(self.root, SUB)) if d.isdigit())
        except FileNotFoundError:
            return []

    @staticmethod
    def _move(src: str, dest: str) -> None:
        try:
            os.rename(src, dest)
        except OSError:
            shutil.copy2(src, dest + ".tmp")            # different filesystem: copy, then appear whole
            os.replace(dest + ".tmp", dest)
            os.remove(src)                              # 3. the spool copy, last

    def closed_in_spool(self, grace_seconds: float, now: float) -> list[str]:
        """Segments in the spool older than the grace: closed, not yet promoted
        (a worker died between close and promote)."""
        out = []
        for d, _, files in os.walk(self.spool):
            for f in files:
                p = os.path.join(d, f)
                if parse(p, self.spool) and now - os.path.getmtime(p) >= grace_seconds:
                    out.append(p)
        return sorted(out)

    def repair(self) -> dict:
        """Make the manifests agree with the files: add lines for files no
        line names (with the epoch from the path), drop lines whose file is
        gone. Idempotent."""
        added = dropped = 0
        for cam in self.cameras():
            man = Manifest(self.root, cam)
            lines = {s.path: s for s in man.read()}
            present = {}
            for d, _, files in os.walk(unit_dir(self.root, SUB, str(cam))):
                for f in files:
                    p = os.path.join(d, f)
                    parsed = parse(p, self.root)
                    if parsed:
                        present[os.path.relpath(p, self.root)] = parsed
            for rel, (c, epoch, start) in present.items():
                if rel not in lines:
                    st = os.stat(os.path.join(self.root, rel))
                    lines[rel] = Segment(c, epoch, start.timestamp(), st.st_mtime, rel, st.st_size)
                    added += 1
            for rel in list(lines):
                if rel not in present:
                    del lines[rel]
                    dropped += 1
            # event buckets: every CLOSED bucket on disk is a line (an open one is still being written);
            # a line whose file is gone is dropped
            known = {b.path: b for b in man.buckets()}
            on_disk = {b.path: b for b in buckets_under(self.root, SUB, str(cam), self.bucket_seconds) if b.end <= self.wall()}
            added += sum(1 for pth in on_disk if pth not in known)
            dropped += sum(1 for pth in known if pth not in on_disk)
            man.rewrite(list(lines.values()), list(on_disk.values()))
        return {"added": added, "dropped": dropped}

    def retain(self, cam: int, days: float, now: float) -> int:
        """Delete media older than `days`: the file first, then the line. The
        buckets are the platform's to retain (vms/retention/<cam>, written by the
        controller); their lines go when repair() finds the files gone."""
        cutoff = now - days * 86400
        man = Manifest(self.root, cam)
        keep, removed = [], 0
        for s in man.read():
            if s.end < cutoff:
                try:
                    os.remove(os.path.join(self.root, s.path))
                except FileNotFoundError:
                    pass
                removed += 1
            else:
                keep.append(s)
        if removed:
            man.rewrite(keep)
        return removed

    def usage(self) -> int:
        total = 0
        for d, _, files in os.walk(self.root):
            for f in files:
                p = os.path.join(d, f)
                if parse(p, self.root):
                    total += os.path.getsize(p)
        return total


class ArchivePolicy:
    """What the VMS registers with the platform's resource job: repair the
    manifests, close the buckets into them, retain media per camera from the
    camera rows. Runs on the resource's timer beside the platform's own pass."""

    def __init__(self, resource: ArchiveResource, vars_):
        self.res, self.vars = resource, vars_

    def pass_(self, now: float) -> dict:
        rep = self.res.repair()
        closed = len(self.res.close_buckets(now, bucket_seconds=self.res.bucket_seconds))
        removed = 0
        for cam in self.res.cameras():
            items, _ = self.vars.get(f"{SUB}/cameras/{cam}")
            days = int(items.get("retention_days", 30)) if items else 30
            removed += self.res.retain(cam, days, now)
        return {**rep, "closed": closed, "media_removed": removed}
