"""Lesson 2 — shadow mode: the domain that writes nothing.

The domain computes what the directory WOULD say, observes what Nodes
report, and emits a divergence report. It changes nothing. The taxonomy:

    lagging       behind, within grace                 not a fault
    stalled       behind past grace, no progress       the real "it did not take effect"
    orphaned      placed, and no Node claims it        fault
    unmanaged     a Node records something the domain never placed   in shadow mode: a MEASUREMENT
    conflict      two Nodes claim one camera           always a fault — fencing or placement
    stale_epoch   a report under a superseded epoch    the fencing rule catching a writer that should have stopped

The one number is `unmanaged == 0`: anything running that the model does
not describe is a gap in the model, and driving it to zero IS the design
work. Ordering beats equality: "behind by 4 for 40 minutes" is an incident;
"diverged" is an alert you learn to ignore.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NodeReport:
    """What a Node's heartbeat/status object says: which cameras it runs,
    under which epoch, at which revision it observed the directory."""
    node: str
    cluster: str
    epoch: int
    cameras: list[int]
    observed_revision: int
    ts: float


@dataclass
class Finding:
    kind: str
    camera: int | None
    node: str | None
    detail: str


@dataclass
class DivergenceReport:
    findings: list[Finding] = field(default_factory=list)
    revision: int = 0

    def count(self, kind: str) -> int:
        return sum(1 for f in self.findings if f.kind == kind)

    @property
    def unmanaged(self) -> int:
        return self.count("unmanaged")

    @property
    def faults(self) -> list[Finding]:
        return [f for f in self.findings if f.kind in ("stalled", "orphaned", "conflict", "stale_epoch")]

    def summary(self) -> str:
        kinds = ("lagging", "stalled", "orphaned", "unmanaged", "conflict", "stale_epoch")
        return "  ".join(f"{k}={self.count(k)}" for k in kinds) + f"  (directory revision {self.revision})"


class Shadow:
    """`placed`: camera -> node the directory says should run it (from М11
    placement/<camera>, cluster by cluster). `epochs`: node -> current epoch
    from nodes/<node>/epoch. `revision`: the directory's current revision."""

    def __init__(self, grace_seconds: float = 60.0):
        self.grace = grace_seconds
        self._progress: dict[str, tuple[int, float]] = {}   # node -> (last observed_revision, since when)

    def compare(self, placed: dict[int, str], epochs: dict[str, int], revision: int,
                reports: list[NodeReport], now: float) -> DivergenceReport:
        rep = DivergenceReport(revision=revision)
        claims: dict[int, list[str]] = {}
        for r in reports:
            if epochs.get(r.node, r.epoch) != r.epoch:
                rep.findings.append(Finding("stale_epoch", None, r.node,
                                            f"reports under epoch {r.epoch}, current is {epochs[r.node]}"))
                continue                                  # a fenced writer's claims count for nothing
            for c in r.cameras:
                claims.setdefault(c, []).append(r.node)
            last, since = self._progress.get(r.node, (None, now))
            if last != r.observed_revision:
                self._progress[r.node] = (r.observed_revision, now)
                since = now
            behind = revision - r.observed_revision
            if behind > 0:
                age = now - since
                kind = "stalled" if age > self.grace else "lagging"
                rep.findings.append(Finding(kind, None, r.node,
                                            f"behind by {behind} revision(s) for {age:.0f}s"))
        for cam, nodes in claims.items():
            if len(nodes) > 1:
                rep.findings.append(Finding("conflict", cam, None, f"claimed by {sorted(nodes)}"))
            elif cam not in placed:
                rep.findings.append(Finding("unmanaged", cam, nodes[0], "running, never placed"))
            elif placed[cam] != nodes[0]:
                rep.findings.append(Finding("conflict", cam, nodes[0], f"placed on {placed[cam]}, running on {nodes[0]}"))
        for cam, node in placed.items():
            if cam not in claims:
                rep.findings.append(Finding("orphaned", cam, node, "placed, and no Node claims it"))
        return rep


def exit_criterion(rep: DivergenceReport, consecutive_clean: int, required: int = 3) -> tuple[bool, str]:
    """The written criterion for switching the domain into write mode."""
    if rep.unmanaged:
        return False, f"unmanaged={rep.unmanaged}: the model does not describe everything that runs"
    if rep.faults:
        return False, f"{len(rep.faults)} fault(s) outstanding: {sorted({f.kind for f in rep.faults})}"
    if consecutive_clean < required:
        return False, f"{consecutive_clean}/{required} consecutive clean reports"
    return True, "unmanaged == 0, no faults, stable across reports: the domain may write"
