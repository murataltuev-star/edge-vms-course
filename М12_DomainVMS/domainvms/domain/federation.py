"""Lesson 1 — what a cluster cannot know.

A domain is N clusters, each with its own raft (Nomad region), its own
Variables and its own object store. Nothing replicates between them. The
domain's directory is therefore an AGGREGATION over N cluster directories:
partial, stale by a bounded amount, sometimes incomplete — and the honest
response to "where is camera 7" when a cluster is unreachable is "not found
in the clusters I could reach", never a short list rendered as complete.

`Cluster` is the domain's handle on one region: a name, its Variables, its
object store, and whether the last read reached it. `Federation` is the
list. `DomainDirectory` is М11's Directory, once per cluster, merged with
the incompleteness kept as a first-class field of every answer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from cluster.directory import NodeDirectory as Directory   # the first design's scan of nodes/*; goes with this module's rewrite to 2c
from cluster.objectstore import ObjectStore
from cluster.variables import Variables


class Unreachable(Exception):
    """The region did not answer. Raised by a cluster's Variables/objects
    when the link is down; the fakes raise it on demand."""


@dataclass
class Cluster:
    name: str
    vars: Variables
    objects: ObjectStore
    reaches: frozenset = frozenset()      # networks this cluster can see: {"vlan:cctv-a", ...}
    is_domain_cluster: bool = False       # the one that hosts the domain services — a stated decision

    def directory(self, ttl: float = 5.0, clock=time.monotonic) -> Directory:
        return Directory(self.vars, ttl=ttl, clock=clock)


@dataclass
class Answer:
    """Where a camera is, and how much of the domain that claim covers."""
    camera: int
    node: str | None
    cluster: str | None
    searched: list[str]
    unreachable: list[str]

    @property
    def complete(self) -> bool:
        return not self.unreachable

    @property
    def found(self) -> bool:
        return self.node is not None

    def sentence(self) -> str:
        if self.found:
            s = f"camera {self.camera} is on {self.node} in {self.cluster}"
            return s if self.complete else s + f" (and {', '.join(self.unreachable)} could not be asked)"
        if self.complete:
            return f"camera {self.camera} is on no Node in the domain ({len(self.searched)} clusters searched)"
        return (f"camera {self.camera} was not found in the {len(self.searched)} cluster(s) I could reach; "
                f"{', '.join(self.unreachable)} unreachable — not 'not anywhere'")


@dataclass
class Federation:
    clusters: dict[str, Cluster] = field(default_factory=dict)

    def add(self, c: Cluster) -> None:
        self.clusters[c.name] = c

    @property
    def domain_cluster(self) -> Cluster:
        hosts = [c for c in self.clusters.values() if c.is_domain_cluster]
        if len(hosts) != 1:
            raise RuntimeError(f"exactly one domain cluster must be designated; found {[c.name for c in hosts]}")
        return hosts[0]


class DomainDirectory:
    """A directory of directories. Reads each cluster's Variables through
    М11's Directory; never copies; never claims more than it reached."""

    def __init__(self, fed: Federation, ttl: float = 5.0, clock=time.monotonic):
        self.fed, self.clock = fed, clock
        self._dirs = {n: c.directory(ttl, clock) for n, c in fed.clusters.items()}

    def scan(self) -> tuple[dict[str, dict[str, dict]], list[str]]:
        """{cluster: {node: holdings}}, and the clusters that did not answer."""
        out, down = {}, []
        for name, d in self._dirs.items():
            try:
                out[name] = d.scan(force=True)
            except Unreachable:
                down.append(name)
        return out, down

    def where(self, camera: int) -> Answer:
        scan, down = self.scan()
        hits = [(cl, node) for cl, nodes in scan.items() for node, h in nodes.items() if camera in h["cameras"]]
        if len(hits) > 1:
            raise RuntimeError(f"camera {camera} claimed by {hits}: a placement or fencing failure, not a tie")
        node, cl = (hits[0][1], hits[0][0]) if hits else (None, None)
        return Answer(camera, node, cl, sorted(scan), sorted(down))

    def holdings(self) -> tuple[dict[str, dict[str, list[int]]], list[str]]:
        scan, down = self.scan()
        return {cl: {n: h["cameras"] for n, h in nodes.items()} for cl, nodes in scan.items()}, down
