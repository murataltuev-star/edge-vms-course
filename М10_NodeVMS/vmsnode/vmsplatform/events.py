"""The event log — a platform piece. What the platform knows about events,
and it is all of this:

    a bucket    <resource>/<subsystem>/<unit>/e<epoch>/<start>Z.events.jsonl
                JSON lines {t, kind, ...}, for a span of `bucket_seconds` starting at <start>
    its writer  the worker that holds that unit's epoch — one writer per file, by construction
    its fence   the epoch in the path: a stale instance writes into its own bucket, marked afterwards
    its index   the manifest beside the unit's buckets, and (М11) a cluster-wide cache over every resource

Nothing here knows what a unit is. The VMS's unit has footage and its
bucket sits beside it; a detector's unit is a detector job; a counter's
unit is a counter. The word "event" means only: something a worker
observed at a time, about a unit it holds.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

EVENTS = re.compile(r"^(\d{8}T\d{6}Z)\.events\.jsonl$")
EPOCH_DIR = re.compile(r"^e(\d+)$")


def bucket_start(t: float, bucket_seconds: int) -> float:
    return float(int(t // bucket_seconds) * bucket_seconds)


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def unit_dir(root: str, subsystem: str, unit: str) -> str:
    return os.path.join(root, subsystem, str(unit))


def bucket_path(root: str, subsystem: str, unit: str, epoch: int, start: float) -> str:
    return os.path.join(unit_dir(root, subsystem, unit), f"e{epoch}", _stamp(start) + ".events.jsonl")


def parse_bucket(path: str, root: str) -> tuple[str, str, int, float] | None:
    """-> (subsystem, unit, epoch, start) for a bucket path under root, else None."""
    rel = os.path.relpath(path, root).split(os.sep)
    if len(rel) != 4 or not EPOCH_DIR.match(rel[2]):
        return None
    m = EVENTS.match(rel[3])
    if not m:
        return None
    start = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    return rel[0], rel[1], int(rel[2][1:]), start


@dataclass(frozen=True)
class Bucket:
    subsystem: str
    unit: str
    epoch: int
    start: float
    end: float
    path: str            # relative to the resource root
    events: int

    def line(self) -> str:
        return json.dumps({"kind": "events", "subsystem": self.subsystem, "unit": self.unit, "epoch": self.epoch,
                           "start": self.start, "end": self.end, "path": self.path, "events": self.events})


class EventLog:
    """What a worker holds per unit it has an epoch for. `append` writes one
    line, flushed, into the bucket for `t`; buckets roll by the clock, not by
    anything the subsystem does."""

    def __init__(self, root: str, subsystem: str, unit: str, epoch: int, bucket_seconds: int = 600):
        self.root, self.subsystem, self.unit, self.epoch, self.bucket_seconds = root, subsystem, str(unit), epoch, bucket_seconds

    def path_for(self, t: float) -> str:
        return bucket_path(self.root, self.subsystem, self.unit, self.epoch, bucket_start(t, self.bucket_seconds))

    def append(self, t: float, kind: str, **fields) -> str:
        p = self.path_for(t)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"t": t, "kind": kind, **fields}) + "\n"); f.flush()
        return p


def read_bucket(path: str) -> list[dict]:
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def buckets_under(root: str, subsystem: str, unit: str, bucket_seconds: int) -> list[Bucket]:
    """Every bucket file for a unit, from the files alone — what repair reads."""
    out = []
    base = unit_dir(root, subsystem, unit)
    for d, _, files in os.walk(base):
        for f in files:
            p = os.path.join(d, f)
            parsed = parse_bucket(p, root)
            if parsed:
                sub, u, epoch, start = parsed
                out.append(Bucket(sub, u, epoch, start, start + bucket_seconds, os.path.relpath(p, root), len(read_bucket(p))))
    return sorted(out, key=lambda b: (b.start, b.epoch))


def subsystems_under(root: str) -> dict[str, list[str]]:
    """{subsystem: [unit, ...]} present on a resource — the index's discovery, no registry."""
    out: dict[str, list[str]] = {}
    try:
        subs = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith("."))
    except FileNotFoundError:
        return out
    for sub in subs:
        units = sorted(u for u in os.listdir(os.path.join(root, sub)) if os.path.isdir(os.path.join(root, sub, u)))
        out[sub] = units
    return out
