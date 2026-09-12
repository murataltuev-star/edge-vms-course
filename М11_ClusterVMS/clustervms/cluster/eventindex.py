"""eventindex — the cluster's event "database", which is a cache.

Events are observations: written by the worker that holds a unit's epoch,
into that unit's bucket on its server's resource (vmsplatform.events).
The VMS's buckets sit beside its footage; a detector's, a counter's, any
subsystem's sit under their own prefix. Cross-unit search needs an index
over all of them, and this is it: one job per cluster, `count = 1`,
holding a SQLite table it can rebuild entirely by re-reading every
resource's buckets. It knows which subsystems exist by what it finds; a
new one is indexed the pass after it starts writing, with no change here.

Its two properties are the controller's, in the form that matters here:
it holds nothing it cannot rebuild, and nothing running depends on it.
No controller writes events. A failed-over eventindex says *catching up*
until its rebuild is done rather than answering short.

    rebuild(resources)   read every subsystem's closed buckets on every resource and index them
    tail(resources)      the same, for buckets it has not seen (by (server, path))
    query(...)           subsystem, unit, cam (a field an event may carry), kind, time window; `unreachable` names silent resources
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.request

from vms.archive import bucket_from_line
from vmsplatform.events import Bucket


class ResourceReader:
    """HTTP against the resource job; tests substitute a directory reader."""
    def __init__(self, timeout: float = 3.0): self.timeout = timeout

    def buckets(self, url: str, sub: str, unit: str) -> list[Bucket]:
        with urllib.request.urlopen(f"{url}/buckets/{sub}/{unit}", timeout=self.timeout) as r:
            return [bucket_from_line(l) for l in r.read().decode().splitlines() if l.strip()]

    def events(self, url: str, b: Bucket) -> list[dict]:
        with urllib.request.urlopen(f"{url}/events/{b.path}", timeout=self.timeout) as r:
            return [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]

    def mirrored(self, url: str, server: str) -> list[Bucket]:
        with urllib.request.urlopen(f"{url}/mirrored/{server}", timeout=self.timeout) as r:
            return [bucket_from_line(l) for l in r.read().decode().splitlines() if l.strip()]

    def mirrored_events(self, url: str, server: str, b: Bucket) -> list[dict]:
        with urllib.request.urlopen(f"{url}/events/.mirror/{server}/{b.path}", timeout=self.timeout) as r:
            return [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]


class EventIndex:
    def __init__(self, reader, path: str = ":memory:", wall=time.time, lost_after: float = 45.0):
        self.reader, self.wall, self.lost_after = reader, wall, lost_after
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS seen   (server TEXT, path TEXT, PRIMARY KEY (server, path));
            CREATE TABLE IF NOT EXISTS events (subsystem TEXT, unit TEXT, cam INTEGER, epoch INTEGER, t REAL, kind TEXT,
                                               server TEXT, path TEXT, fields TEXT);
            CREATE INDEX IF NOT EXISTS events_cam_t ON events (cam, t);
            CREATE INDEX IF NOT EXISTS events_sub_unit_t ON events (subsystem, unit, t);
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
        now = self.wall(); added = 0; unreachable = []; from_mirror = []
        live = {s: hb for s, hb in resources.items() if now - float(hb["ts"]) <= self.lost_after}
        for server, hb in sorted(resources.items()):
            if server not in live:
                if self._from_mirror(server, live):
                    from_mirror.append(server); added += self._last_mirror_added
                else:
                    unreachable.append(server)
                continue
            try:
                for sub, units in hb.get("units", {}).items():
                    for unit in units:
                        for b in self.reader.buckets(hb["url"], sub, unit):
                            if b.events == 0 or self.db.execute("SELECT 1 FROM seen WHERE server=? AND path=?", (server, b.path)).fetchone():
                                continue
                            rows = []
                            for e in self.reader.events(hb["url"], b):
                                cam = e.get("cam", int(unit) if sub == "vms" and unit.isdigit() else None)   # the VMS's unit IS the camera; others may point at one
                                rows.append((sub, unit, cam, b.epoch, float(e["t"]), e["kind"], server, b.path,
                                             json.dumps({k: v for k, v in e.items() if k not in ("t", "kind", "cam")})))
                            with self.db:
                                self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", rows)
                                self.db.execute("INSERT INTO seen VALUES (?,?)", (server, b.path))
                            added += len(rows); self.indexed_segments += 1
            except Exception:                          # noqa: BLE001 — fresh heartbeat, server not answering
                unreachable.append(server)
        self.state = "live" + (f"; {', '.join(unreachable)} unreachable" if unreachable else "") \
                            + (f"; {', '.join(from_mirror)} from mirror" if from_mirror else "")
        return {"added": added, "unreachable": unreachable, "from_mirror": from_mirror, "segments": self.indexed_segments}

    def _from_mirror(self, server: str, live: dict[str, dict]) -> bool:
        """A silent server's closed buckets, from whichever live peer holds
        copies (its heartbeat says: mirrors). Rows are inserted under the
        REAL server: only the source differed. Never used while the resource answers."""
        self._last_mirror_added = 0
        holders = [(s, hb) for s, hb in live.items() if server in hb.get("mirrors", {})]
        if not holders:
            return False
        for peer, hb in holders:
            try:
                for b in self.reader.mirrored(hb["url"], server):
                    if b.events == 0 or self.db.execute("SELECT 1 FROM seen WHERE server=? AND path=?", (server, b.path)).fetchone():
                        continue
                    rows = []
                    for e in self.reader.mirrored_events(hb["url"], server, b):
                        cam = e.get("cam", int(b.unit) if b.subsystem == "vms" and b.unit.isdigit() else None)
                        rows.append((b.subsystem, b.unit, cam, b.epoch, float(e["t"]), e["kind"], server, b.path,
                                     json.dumps({k: v for k, v in e.items() if k not in ("t", "kind", "cam")})))
                    with self.db:
                        self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", rows)
                        self.db.execute("INSERT INTO seen VALUES (?,?)", (server, b.path))
                    self._last_mirror_added += len(rows); self.indexed_segments += 1
            except Exception:                        # noqa: BLE001 — that peer is not answering either
                continue
        return True

    def query(self, t0: float, t1: float, cam: int | None = None, kind: str | None = None,
              subsystem: str | None = None, unit: str | None = None,
              current_epochs: dict[tuple[str, str], int] | None = None, limit: int = 1000) -> dict:
        """`current_epochs` is {(subsystem, unit): epoch} — fencing is per unit, and only the
        unit's own subsystem knows its current epoch; the index just compares."""
        sql, args = "SELECT subsystem, unit, cam, epoch, t, kind, server, path, fields FROM events WHERE t >= ? AND t < ?", [t0, t1]
        if cam is not None: sql += " AND cam = ?"; args.append(cam)
        if kind is not None: sql += " AND kind = ?"; args.append(kind)
        if subsystem is not None: sql += " AND subsystem = ?"; args.append(subsystem)
        if unit is not None: sql += " AND unit = ?"; args.append(str(unit))
        sql += " ORDER BY t LIMIT ?"; args.append(limit)
        out = []
        for sub, u, c, ep, t, k, server, path, fields in self.db.execute(sql, args):
            cur = (current_epochs or {}).get((sub, u))
            out.append({"subsystem": sub, "unit": u, "cam": c, "epoch": ep, "t": t, "kind": k, "server": server, "bucket": path,
                        "fenced": cur is not None and ep < cur, **json.loads(fields)})
        return {"events": out, "state": self.state}

    def forget(self, server: str, paths: list[str]) -> int:
        """Retention on a resource removed a segment: its events go with it."""
        with self.db:
            for p in paths:
                self.db.execute("DELETE FROM events WHERE server=? AND path=?", (server, p))
                self.db.execute("DELETE FROM seen WHERE server=? AND path=?", (server, p))
        return len(paths)
