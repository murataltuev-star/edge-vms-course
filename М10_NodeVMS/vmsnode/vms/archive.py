"""The archive as a resource — server-bound, no controller, a policy.

    <spool>/<cam>/e<epoch>/<start>Z.mp4       the open segment, and closed ones not yet promoted
    <archive>/<cam>/e<epoch>/<start>Z.mp4     promoted: the resource
    <archive>/<cam>/manifest.jsonl            one line per promoted segment: the index that lives beside the footage

The acknowledgement order is М9 Lesson 4's: a closed segment is PROMOTED
(renamed into the archive, then a manifest line appended), and the spool
copy is gone only after that. A segment appears in the archive whole or
not at all. The manifest is append-only and rebuildable from the files.

Retention is a policy ON the resource: lines and files older than N days
per camera, files first, so a crash between the two leaves a line that
names nothing rather than a file nothing names — `repair()` fixes either.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone

SEGMENT = re.compile(r"^(\d{8}T\d{6}Z)\.mp4$")
EPOCH_DIR = re.compile(r"^e(\d+)$")


def segment_path(root: str, cam: int, epoch: int, start: datetime) -> str:
    return os.path.join(root, str(cam), f"e{epoch}", start.strftime("%Y%m%dT%H%M%SZ") + ".mp4")


def parse(path: str, root: str) -> tuple[int, int, datetime] | None:
    rel = os.path.relpath(path, root).split(os.sep)
    if len(rel) != 3 or not rel[0].isdigit() or not EPOCH_DIR.match(rel[1]):
        return None
    m = SEGMENT.match(rel[2])
    if not m:
        return None
    return int(rel[0]), int(rel[1][1:]), datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Segment:
    cam: int
    epoch: int
    start: float          # unix seconds
    end: float
    path: str             # relative to the archive root
    bytes: int

    def line(self) -> str:
        return json.dumps({"cam": self.cam, "epoch": self.epoch, "start": self.start, "end": self.end,
                           "path": self.path, "bytes": self.bytes})

    @classmethod
    def from_line(cls, line: str) -> "Segment":
        d = json.loads(line)
        return cls(int(d["cam"]), int(d["epoch"]), float(d["start"]), float(d["end"]), d["path"], int(d["bytes"]))


class Manifest:
    """Per camera, append-only, beside the footage."""

    def __init__(self, archive_root: str, cam: int):
        self.path = os.path.join(archive_root, str(cam), "manifest.jsonl")

    def append(self, seg: Segment) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a") as f:
            f.write(seg.line() + "\n")

    def read(self) -> list[Segment]:
        try:
            with open(self.path) as f:
                return [Segment.from_line(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []

    def rewrite(self, segs: list[Segment]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for s in sorted(segs, key=lambda s: (s.start, s.epoch)):
                f.write(s.line() + "\n")
        os.replace(tmp, self.path)

    def timeline(self, t0: float, t1: float, current_epoch: int | None = None) -> list[dict]:
        """Segments overlapping [t0, t1), each marked *fenced* if its epoch is older than the current."""
        out = []
        for s in self.read():
            if s.end > t0 and s.start < t1:
                out.append({"start": s.start, "end": s.end, "path": s.path, "epoch": s.epoch,
                            "fenced": current_epoch is not None and s.epoch < current_epoch})
        return sorted(out, key=lambda d: (d["start"], d["epoch"]))


class ArchiveResource:
    """One server's archive. `promote()` is what archivesink calls on
    fragment-closed; `repair()` is what М11 called the re-index sweep."""

    def __init__(self, spool_root: str, archive_root: str):
        self.spool, self.root = spool_root, archive_root
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
        try:
            os.rename(spool_path, dest)                 # 1. into the archive, atomically (same filesystem)
        except OSError:
            shutil.copy2(spool_path, dest + ".tmp")     # different filesystem: copy, then appear whole
            os.replace(dest + ".tmp", dest)
            os.remove(spool_path)                       # 3. the spool copy, last
        seg = Segment(cam, epoch, start.timestamp(), end, rel, st.st_size)
        Manifest(self.root, cam).append(seg)            # 2. then the line
        return seg

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
        cams = {int(d) for d in os.listdir(self.root) if d.isdigit()}
        for cam in cams:
            man = Manifest(self.root, cam)
            lines = {s.path: s for s in man.read()}
            present = {}
            for d, _, files in os.walk(os.path.join(self.root, str(cam))):
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
            man.rewrite(list(lines.values()))
        return {"added": added, "dropped": dropped}

    def retain(self, cam: int, days: float, now: float) -> int:
        """Delete segments older than `days`: the file first, then the line."""
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
