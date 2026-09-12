"""eventindex — the cluster's event "database", which is a cache.

Events are observations: written by the worker that observed them, under
its epoch, beside the segment they describe (М10 Lesson 3). They live on
the archive resource with the footage and are promoted, retained and
fenced with it. Cross-camera search needs an index over them, and this is
it: one job per cluster, `count = 1`, holding a SQLite table it can
rebuild entirely by re-reading the resources' manifests and event files.

Its two properties are the controller's, in the form that matters here:
it holds nothing it cannot rebuild, and nothing running depends on it.
No controller writes events. A failed-over eventindex says *catching up*
until its rebuild is done rather than answering short.

    rebuild(resources)   read every manifest line with events > 0 and index its file
    tail(resources)      the same, for lines it has not seen (by (server, path))
    query(...)           cam, kind, time window, across the cluster; `unreachable` names silent resources
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

from vms.archive import Segment


class ResourceReader:
    """HTTP against the resource job; tests substitute a directory reader."""
    def __init__(self, timeout: float = 3.0): self.timeout = timeout

    def manifest(self, url: str, cam: int) -> list[Segment]:
        with urllib.request.urlopen(f"{url}/manifest/{cam}", timeout=self.timeout) as r:
            return [Segment.from_line(l) for l in r.read().decode().splitlines() if l.strip()]

    def events(self, url: str, seg: Segment) -> list[dict]:
        with urllib.request.urlopen(f"{url}/events/{seg.path[:-4]}.events.jsonl", timeout=self.timeout) as r:
            return [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]


class EventIndex:
    def __init__(self, reader, path: str = ":memory:", wall=time.time, lost_after: float = 45.0):
        self.reader, self.wall, self.lost_after = reader, wall, lost_after
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS seen   (server TEXT, path TEXT, PRIMARY KEY (server, path));
            CREATE TABLE IF NOT EXISTS events (cam INTEGER, epoch INTEGER, t REAL, kind TEXT, server TEXT, path TEXT, fields TEXT);
            CREATE INDEX IF NOT EXISTS events_cam_t ON events (cam, t);
            CREATE INDEX IF NOT EXISTS events_kind_t ON events (kind, t);""")
        self.state = "empty"
        self.indexed_segments = 0

    def rebuild(self, resources: dict[str, dict]) -> dict:
        """From nothing: what a failed-over instance does first."""
        self.db.executescript("DELETE FROM seen; DELETE FROM events;")
        self.indexed_segments = 0
        return self.tail(resources, rebuild=True)

    def tail(self, resources: dict[str, dict], rebuild: bool = False) -> dict:
        self.state = "catching up" if rebuild else self.state
        now = self.wall(); added = 0; unreachable = []
        for server, hb in sorted(resources.items()):
            if now - float(hb["ts"]) > self.lost_after:
                unreachable.append(server); continue
            try:
                for cam in hb.get("cameras", []):
                    for seg in self.reader.manifest(hb["url"], cam):
                        if seg.events == 0 or self.db.execute("SELECT 1 FROM seen WHERE server=? AND path=?", (server, seg.path)).fetchone():
                            continue
                        rows = [(seg.cam, seg.epoch, float(e["t"]), e["kind"], server, seg.path,
                                 json.dumps({k: v for k, v in e.items() if k not in ("t", "kind")})) for e in self.reader.events(hb["url"], seg)]
                        with self.db:
                            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", rows)
                            self.db.execute("INSERT INTO seen VALUES (?,?)", (server, seg.path))
                        added += len(rows); self.indexed_segments += 1
            except Exception:                          # noqa: BLE001 — fresh heartbeat, server not answering
                unreachable.append(server)
        self.state = "live" if not unreachable else f"live; {', '.join(unreachable)} unreachable"
        return {"added": added, "unreachable": unreachable, "segments": self.indexed_segments}

    def query(self, t0: float, t1: float, cam: int | None = None, kind: str | None = None,
              current_epochs: dict[int, int] | None = None, limit: int = 1000) -> dict:
        sql, args = "SELECT cam, epoch, t, kind, server, path, fields FROM events WHERE t >= ? AND t < ?", [t0, t1]
        if cam is not None: sql += " AND cam = ?"; args.append(cam)
        if kind is not None: sql += " AND kind = ?"; args.append(kind)
        sql += " ORDER BY t LIMIT ?"; args.append(limit)
        out = []
        for c, ep, t, k, server, path, fields in self.db.execute(sql, args):
            cur = (current_epochs or {}).get(c)
            out.append({"cam": c, "epoch": ep, "t": t, "kind": k, "server": server, "segment": path,
                        "fenced": cur is not None and ep < cur, **json.loads(fields)})
        return {"events": out, "state": self.state}

    def forget(self, server: str, paths: list[str]) -> int:
        """Retention on a resource removed a segment: its events go with it."""
        with self.db:
            for p in paths:
                self.db.execute("DELETE FROM events WHERE server=? AND path=?", (server, p))
                self.db.execute("DELETE FROM seen WHERE server=? AND path=?", (server, p))
        return len(paths)
