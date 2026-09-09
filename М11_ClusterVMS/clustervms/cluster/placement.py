"""Lesson 5 — placement onto Nodes: capacity measured, constraints as
labels, the placement STORED in Variables (placement/<camera>), and one
rule with a property test: adding a Node moves nothing.

Rebalance is explicit, budgeted, observable, interruptible, with a dead
band — and each move is the one two-writer operation in the module, so
the caller performs it with the epoch: stop on the old Node, start on the
new, never both at once without a fence.
"""
from __future__ import annotations

from dataclasses import dataclass

from .variables import Variables


@dataclass(frozen=True)
class Camera:
    id: int
    load: float                       # in units of the probe's per-pipeline increment I
    labels: frozenset = frozenset()   # e.g. {"vlan:cctv-b"}


@dataclass
class Node:
    id: str
    capacity: float                   # (budget − B) / I from shard-memory-probe.py
    labels: frozenset = frozenset()


@dataclass
class Placement:
    node: str
    reason: str
    rev: int


class Placer:
    """Placements live in Variables under placement/<camera_id>: small,
    one writer (the placement service), exact lookups only."""

    def __init__(self, nodes: dict[str, Node], vars_: Variables):
        self.nodes = nodes
        self.vars = vars_
        self.placed: dict[int, Placement] = {}
        self._load()

    def _load(self):
        for path in self.vars.list("placement/"):
            items, _ = self.vars.get(path)
            if items:
                self.placed[int(path.split("/")[1])] = Placement(items["node"], items["reason"], int(items["rev"]))

    def _store(self, cam_id: int, pl: Placement):
        _, idx = self.vars.get(f"placement/{cam_id}")
        self.vars.put(f"placement/{cam_id}", {"node": pl.node, "reason": pl.reason, "rev": pl.rev}, cas=idx)
        self.placed[cam_id] = pl

    def _rev(self) -> int:
        items, _ = self.vars.get("placement")
        rev = int(items["rev"]) + 1 if items else 1
        self.vars.put("placement", {"rev": rev})
        return rev

    # -- queries --------------------------------------------------------------
    def load_of(self, node_id: str, cameras: dict[int, Camera]) -> float:
        return sum(cameras[c].load for c, p in self.placed.items() if p.node == node_id and c in cameras)

    def eligible(self, cam: Camera) -> list[Node]:
        return [n for n in self.nodes.values() if cam.labels <= n.labels]

    # -- placement ------------------------------------------------------------
    def place(self, cam: Camera, cameras: dict[int, Camera]) -> Placement | None:
        """Place ONE new camera. Existing placements are never touched."""
        if cam.id in self.placed:
            return self.placed[cam.id]
        best, best_free = None, 0.0
        for n in sorted(self.eligible(cam), key=lambda n: n.id):       # deterministic tiebreak
            free = n.capacity - self.load_of(n.id, cameras)
            if free >= cam.load and free > best_free:
                best, best_free = n, free
        if best is None:
            return None                           # "the system is full" — never "Node 3 is full"
        pl = Placement(best.id, f"most free capacity ({best_free:.1f}) among {len(self.eligible(cam))} eligible",
                       self._rev())
        self._store(cam.id, pl)
        return pl

    def remove(self, cam_id: int) -> None:
        if cam_id in self.placed:
            _, idx = self.vars.get(f"placement/{cam_id}")
            self.vars.put(f"placement/{cam_id}", {"node": "", "reason": "removed", "rev": self._rev()}, cas=idx)
            del self.placed[cam_id]

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node                # nothing moves. That is the rule.

    def retire_node(self, node_id: str, cameras: dict[int, Camera]) -> list[int]:
        """A Node retired ON PURPOSE (a failover moves the Node, not its cameras)."""
        del self.nodes[node_id]
        orphans = [c for c, p in self.placed.items() if p.node == node_id]
        homeless = []
        for c in orphans:
            del self.placed[c]
            if self.place(cameras[c], cameras) is None:
                homeless.append(c)
        return homeless

    # -- rebalance ------------------------------------------------------------
    def rebalance(self, cameras: dict[int, Camera], budget: int, dead_band: float = 0.10) -> list[tuple[int, str, str]]:
        moves = []
        for _ in range(budget):
            loads = {n: self.load_of(n, cameras) / self.nodes[n].capacity for n in self.nodes}
            if not loads:
                break
            hi = max(loads, key=loads.get); lo = min(loads, key=loads.get)
            if loads[hi] - loads[lo] < dead_band:
                break
            candidates = [c for c, p in self.placed.items() if p.node == hi and c in cameras
                          and cameras[c].labels <= self.nodes[lo].labels]
            if not candidates:
                break
            c = min(candidates, key=lambda c: cameras[c].load)
            if self.load_of(lo, cameras) + cameras[c].load > self.nodes[lo].capacity:
                break
            self._store(c, Placement(lo, f"rebalance from {hi} (spread {loads[hi]-loads[lo]:.0%})", self._rev()))
            moves.append((c, hi, lo))
        return moves


def check_invariants(p: Placer, cameras: dict[int, Camera]) -> None:
    for cid, pl in p.placed.items():
        assert pl.node in p.nodes, f"camera {cid} placed on a Node that does not exist"
        assert cameras[cid].labels <= p.nodes[pl.node].labels, f"camera {cid} violates its constraint"
    for n in p.nodes.values():
        assert p.load_of(n.id, cameras) <= n.capacity + 1e-9, f"{n.id} over capacity"
