"""М9 Lesson 6 — the reconcile loop, with nothing in it. Copied here
unchanged, because this is the contract: the same `>=`, the same stop loop
over what is RUNNING, the same jitter. М9's seven tests run against it in
tests/test_worker.py without a change of meaning.

    Desired state is persisted. Actual state is derived.
"""
from __future__ import annotations

import random
from typing import Callable, Protocol

CONVERGED, LAGGING, STALLED = "converged", "lagging", "stalled"


class Store(Protocol):
    def desired(self) -> list[dict]: ...


Actuator = Callable[[str, dict], bool]


class Reconciler:
    def __init__(self, store: Store, actuator: Actuator, max_backoff: float = 60.0, stall_failures: int = 3):
        self.store, self.actuator = store, actuator
        self.actual: dict[int, dict] = {}       # camera_id -> {"revision": n}   IN MEMORY ONLY
        self.failures: dict[int, dict] = {}
        self.max_backoff, self.stall_failures = max_backoff, stall_failures

    def reconcile(self, now: float = 0.0) -> list[tuple[str, int]]:
        desired = {c["id"]: c for c in self.store.desired() if c["enabled"]}
        actions: list[tuple[str, int]] = []
        for cid, cam in desired.items():
            have = self.actual.get(cid)
            if have and have["revision"] >= cam["revision"]:
                continue
            if self.failures.get(cid) and now < self.failures[cid]["retry_at"]:
                continue
            verb = "start" if not have else "restart"
            if self.actuator(verb, cam):
                self.actual[cid] = {"revision": cam["revision"]}
                self.failures.pop(cid, None)
                actions.append((verb, cid))
            else:
                self._fail(cid, now)
                actions.append(("failed", cid))
        for cid in list(self.actual):                 # the stop loop walks what is RUNNING
            if cid not in desired:
                self.actuator("stop", {"id": cid})
                del self.actual[cid]
                actions.append(("stop", cid))
        return actions

    def _fail(self, cid: int, now: float) -> None:
        n = self.failures.get(cid, {}).get("n", 0) + 1
        base = min(2 ** n, self.max_backoff)
        delay = base * (0.5 + random.random() * 0.5)
        self.failures[cid] = {"n": n, "retry_at": now + delay, "delay": delay}

    def lost(self, cid: int, now: float) -> None:
        self.actual.pop(cid, None)
        self._fail(cid, now)

    def clear(self) -> None:
        """What a fence does: the pipelines were stopped underneath the loop."""
        self.actual.clear()

    def status(self) -> dict[int, tuple[str, int]]:
        out: dict[int, tuple[str, int]] = {}
        for cam in self.store.desired():
            if not cam["enabled"]:
                continue
            cid = cam["id"]
            have = self.actual.get(cid, {}).get("revision", 0)
            lag = max(cam["revision"] - have, 0)
            if lag == 0:
                out[cid] = (CONVERGED, 0)
            elif self.failures.get(cid, {}).get("n", 0) >= self.stall_failures:
                out[cid] = (STALLED, lag)
            else:
                out[cid] = (LAGGING, lag)
        return out
