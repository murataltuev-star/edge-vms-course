"""Fakes: a Variables raft in memory, an object store on disk, servers with
archive directories, a clock. No Nomad, no MinIO, no GStreamer.

`FakeClusterStore` and `cam` at the bottom are the first design's fixtures,
kept because М12's `domainvms` tests still import them through
`tests_cluster_conftest.py`; they go when М12 is rewritten to 2c."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cluster  # noqa: E402,F401  — puts М10's vmsnode on sys.path

from cluster.configio import decode, encode  # noqa: E402
from cluster.objectstore import FsObjectStore  # noqa: E402
from cluster.variables import FakeVariables  # noqa: E402
from cluster.worker import ClusterWorker  # noqa: E402
from vms.archive import ArchiveResource  # noqa: E402
from vms.worker import FakeActuator  # noqa: E402


class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


class Server:
    """A box in the cluster: a name, labels, an archive resource on its disks."""
    def __init__(self, root: str, name: str, labels: str = "", wall=None):
        self.name, self.labels = name, labels
        self.spool, self.archive = os.path.join(root, name, "spool"), os.path.join(root, name, "archive")
        self.resource = ArchiveResource(self.spool, self.archive, wall=wall)


class Cluster:
    """Three servers, one raft, one object store — and a clock the tests own."""
    def __init__(self, servers=(("srv-a", "vlan:cctv-a"), ("srv-b", "vlan:cctv-a,vlan:cctv-b"), ("srv-c", "vlan:cctv-b"))):
        self.root = tempfile.mkdtemp(prefix="clustervms-")
        self.vars = FakeVariables()
        self.objects = FsObjectStore(os.path.join(self.root, "objects"))
        self.clock, self.wall = Clock(), Clock(1_757_500_000.0)
        self.servers = {n: Server(self.root, n, l, self.wall) for n, l in servers}
        self.allocs = 0

    def env(self, index: int, server: str, alloc: str | None = None) -> dict:
        """What Nomad puts in an allocation's environment."""
        self.allocs += 1
        return {"NOMAD_ALLOC_INDEX": str(index), "NOMAD_NODE_NAME": server,
                "NOMAD_META_labels": self.servers[server].labels,
                "NOMAD_ALLOC_ID": alloc or f"alloc-{self.allocs:04d}"}

    def worker(self, index: int, server: str, capacity: int = 50, actuator=None, alloc=None) -> ClusterWorker:
        w = ClusterWorker(self.vars.as_writer(f"vmsworker", ["vms/epoch/*", "vms/slots/*"]), self.objects,
                          actuator or FakeActuator(), env=self.env(index, server, alloc),
                          clock=self.clock, wall=self.wall, capacity=capacity)
        return w


def world():
    return FakeVariables(), FsObjectStore(tempfile.mkdtemp(prefix="restore-"))


# ---- the first design's fixtures, for М12's tests -------------------------------------------
def cam(i, revision=1, enabled=True, **kw):
    return {"id": i, "site_id": "hq", "name": f"cam{i}", "rtsp_url": f"rtsp://10.0.0.{i}/s",
            "cred_username": None, "cred_secret": None, "enabled": enabled,
            "retention_days": kw.get("retention_days", 30), "priority": kw.get("priority", 100),
            "revision": revision}


class FakeClusterStore:
    """М9's PgStore as the first ClusterVMS saw it, in memory."""

    def __init__(self, cameras=None):
        self.rows: dict[int, dict] = {c["id"]: dict(c) for c in (cameras or [])}
        self.sites = [{"id": "hq", "name": "hq"}] if self.rows else []
        self.reports: list = []
        self.conditions: dict = {}
        self.segments: list = []
        self.events: list = []
        self.migrated = 0

    async def migrate(self, d): self.migrated += 1; return True
    async def listen(self, ch, cb): return None
    async def fetch_desired(self): return [dict(r) for r in self.rows.values()]
    async def report(self, rows): self.reports.append(list(rows))
    async def set_condition(self, cid, cond, status, reason=None): self.conditions[(cid, cond)] = (status, reason)
    async def index_segment(self, *row): self.segments.append(row)
    async def indexed_paths_under(self, prefix): return {r[3] for r in self.segments if r[3].startswith(prefix)}
    async def log_event(self, kind, camera_id=None, payload=None): self.events.append((kind, camera_id, payload))
    async def cameras(self): return [dict(r) for r in self.rows.values()]
    async def dump_config(self): return encode(self.sites, list(self.rows.values()), [], [])
    async def config_revision(self): return max((r["revision"] for r in self.rows.values()), default=0)
    async def is_unconfigured(self): return not self.rows
    async def restore_config(self, blob):
        d = decode(blob)
        self.sites = d["sites"]
        self.rows = {c["id"]: c for c in d["cameras"]}
        return int(d["revision"])

    def edit(self, cid, **fields):
        self.rows.setdefault(cid, cam(cid, revision=0))
        self.rows[cid].update(fields)
        self.rows[cid]["revision"] = max(r["revision"] for r in self.rows.values()) + 1
        return self.rows[cid]["revision"]
