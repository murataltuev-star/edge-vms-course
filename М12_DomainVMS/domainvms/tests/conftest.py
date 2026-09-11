"""Fakes: N clusters, each a Variables raft and an object store, with a
switch to make one unreachable; Nodes that publish heartbeats; a clock."""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import domain  # noqa: E402,F401

from cluster.objectstore import FsObjectStore  # noqa: E402
from cluster.publish import Publisher  # noqa: E402
from cluster.variables import FakeVariables  # noqa: E402
from domain.federation import Cluster, Federation, Unreachable  # noqa: E402
from tests.tests_cluster_conftest import FakeClusterStore, cam  # noqa: E402


class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


class Link:
    """One switch per cluster: when down, every read raises Unreachable."""
    def __init__(self): self.up = True


class LinkedVariables(FakeVariables):
    def __init__(self, link: Link):
        super().__init__(); self.link = link
    def _check(self):
        if not self.link.up: raise Unreachable("region did not answer")
    def get(self, path): self._check(); return super().get(path)
    def put(self, path, items, cas=None): self._check(); return super().put(path, items, cas)
    def list(self, prefix): self._check(); return super().list(prefix)
    def as_writer(self, writer):
        v = LinkedVariables.__new__(LinkedVariables); v.__dict__ = self.__dict__.copy(); v.writer = writer; return v


class LinkedObjects(FsObjectStore):
    def __init__(self, root, link: Link):
        super().__init__(root); self.link = link
    def _check(self):
        if not self.link.up: raise Unreachable("region did not answer")
    def put(self, k, d): self._check(); return super().put(k, d)
    def get(self, k): self._check(); return super().get(k)


def make_cluster(name: str, reaches=(), domain: bool = False) -> tuple[Cluster, Link]:
    link = Link()
    c = Cluster(name, LinkedVariables(link), LinkedObjects(tempfile.mkdtemp(prefix=f"{name}-"), link),
                frozenset(reaches), domain)
    return c, link


def make_domain(spec: dict[str, tuple], domain_cluster: str) -> tuple[Federation, dict[str, Link]]:
    """spec: {"north": ("vlan:a", "vlan:b"), ...}"""
    fed, links = Federation(), {}
    for name, reaches in spec.items():
        c, link = make_cluster(name, reaches, name == domain_cluster)
        fed.add(c); links[name] = link
    return fed, links


async def publish_node(cluster: Cluster, node: str, cams: list[int], revision: int = 1) -> None:
    """A Node's Variable (М11 publish) in this cluster."""
    store = FakeClusterStore([cam(c, revision=revision) for c in cams])
    await Publisher(node, store, cluster.vars, cluster.objects, 0).publish_once()


def heartbeat(cluster: Cluster, node: str, cams: list[int], ts: float, epoch: int = 1, server: str = "srv-1",
              phase: str = "running", revision: int = 1) -> None:
    """What М11's heartbeat task writes since М12: the status snapshot."""
    cluster.objects.put(f"{node}/heartbeat", json.dumps({
        "ts": ts, "epoch": epoch, "server": server, "revision": revision,
        "cameras": [{"id": c, "name": f"cam{c}", "site": "hq", "enabled": True, "phase": phase,
                     "revision": revision, "observed_revision": revision} for c in cams]}).encode())
