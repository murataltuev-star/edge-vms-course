"""Fakes: a Variables raft in memory, an object store on disk, and the async
surface of М9's PgStore that ClusterVMS touches. No Nomad, no Postgres."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cluster  # noqa: E402,F401

from cluster.configio import decode, encode  # noqa: E402
from cluster.objectstore import FsObjectStore  # noqa: E402
from cluster.variables import FakeVariables  # noqa: E402


def cam(i, revision=1, enabled=True, **kw):
    return {"id": i, "site_id": "hq", "name": f"cam{i}", "rtsp_url": f"rtsp://10.0.0.{i}/s",
            "cred_username": None, "cred_secret": None, "enabled": enabled,
            "retention_days": kw.get("retention_days", 30), "priority": kw.get("priority", 100),
            "revision": revision}


class FakeClusterStore:
    """М9's PgStore as ClusterVMS sees it, in memory."""

    def __init__(self, cameras=None):
        self.rows: dict[int, dict] = {c["id"]: dict(c) for c in (cameras or [])}
        self.sites = [{"id": "hq", "name": "hq"}] if self.rows else []
        self.reports: list = []
        self.conditions: dict = {}
        self.segments: list = []
        self.events: list = []
        self.migrated = 0

    # М9 surface
    async def migrate(self, d): self.migrated += 1; return True
    async def listen(self, ch, cb): return None
    async def fetch_desired(self): return [dict(r) for r in self.rows.values()]
    async def report(self, rows): self.reports.append(list(rows))
    async def set_condition(self, cid, cond, status, reason=None): self.conditions[(cid, cond)] = (status, reason)
    async def index_segment(self, *row): self.segments.append(row)
    async def indexed_paths_under(self, prefix): return {r[3] for r in self.segments if r[3].startswith(prefix)}
    async def log_event(self, kind, camera_id=None, payload=None): self.events.append((kind, camera_id, payload))
    async def cameras(self): return [dict(r) for r in self.rows.values()]
    # ClusterStoreMixin surface
    async def dump_config(self): return encode(self.sites, list(self.rows.values()), [], [])
    async def config_revision(self): return max((r["revision"] for r in self.rows.values()), default=0)
    async def is_unconfigured(self): return not self.rows
    async def restore_config(self, blob):
        d = decode(blob)
        self.sites = d["sites"]
        self.rows = {c["id"]: c for c in d["cameras"]}
        return int(d["revision"])
    # a helper the tests use as "the operator edited a camera"
    def edit(self, cid, **fields):
        self.rows.setdefault(cid, cam(cid, revision=0))
        self.rows[cid].update(fields)
        self.rows[cid]["revision"] = max(r["revision"] for r in self.rows.values()) + 1
        return self.rows[cid]["revision"]


def world():
    return FakeVariables(), FsObjectStore(tempfile.mkdtemp(prefix="restore-"))


class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s
