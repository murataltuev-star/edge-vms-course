"""Lesson 3 — the API, and what it refuses.

The console's write API is a façade: an edit goes to the directory ("where
is camera 7"), then to the OWNING Node's console, and that Node's grants
decide. The console owns nothing and never writes on its own account.

    idempotency keys    a retried PUT is the same PUT, not a second edit
    what it refuses     a client may not set placement (node, cluster) — placement is stored by
                        the placement service with a reason, never dictated by an edit
    positions/reasons   the read model's `phase` and `conditions` are passed through untouched
    authentication      Lesson 3 ships it unauthenticated and says so; Lesson 4 adds `verifier`
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .federation import DomainDirectory

FORBIDDEN_FIELDS = ("node", "cluster", "placement", "epoch", "observed_revision", "phase")


class NodeConsole(Protocol):
    """What the console can ask of a Node's own console (М10 Lesson 5, М11 Lesson 5)."""
    def update_camera(self, camera: int, fields: dict, subject: str | None) -> dict: ...
    def create_camera(self, fields: dict, subject: str | None) -> dict: ...


@dataclass
class ApiError(Exception):
    status: int
    detail: str

    def __str__(self) -> str:
        return f"{self.status}: {self.detail}"


class ConsoleAPI:
    def __init__(self, directory: DomainDirectory, consoles: Callable[[str], NodeConsole],
                 verifier: Callable[[str], str] | None = None):
        """`consoles(node)` finds the Node's console — service discovery, in
        production; a dict in tests. `verifier(token) -> subject` is Lesson 4;
        None means unauthenticated, and the API says so on every response."""
        self.directory, self.consoles, self.verifier = directory, consoles, verifier
        self._seen: dict[str, dict] = {}                      # idempotency key -> response

    def _subject(self, token: str | None) -> str | None:
        if self.verifier is None:
            return None
        if not token:
            raise ApiError(401, "a token is required")
        return self.verifier(token)

    def _refuse_placement(self, fields: dict) -> None:
        bad = [k for k in fields if k in FORBIDDEN_FIELDS]
        if bad:
            raise ApiError(400, f"a client may not set {bad}: placement is decided and stored by the placement "
                                f"service with a reason; phase and observed_revision are controller-owned")

    def update_camera(self, camera: int, fields: dict, idempotency_key: str, token: str | None = None) -> dict:
        if idempotency_key in self._seen:
            return self._seen[idempotency_key]                # the same PUT, not a second edit
        self._refuse_placement(fields)
        subject = self._subject(token)
        ans = self.directory.where(camera)
        if not ans.found:
            raise ApiError(404 if ans.complete else 503, ans.sentence())
        result = self.consoles(ans.node).update_camera(camera, fields, subject)
        resp = {"camera": camera, "node": ans.node, "cluster": ans.cluster, "result": result,
                "authenticated": self.verifier is not None}
        self._seen[idempotency_key] = resp
        return resp

    def create_camera(self, fields: dict, node: str, idempotency_key: str, token: str | None = None) -> dict:
        """`node` comes from the placement service's stored decision, which
        the console reads and forwards — it does not choose."""
        if idempotency_key in self._seen:
            return self._seen[idempotency_key]
        self._refuse_placement(fields)
        subject = self._subject(token)
        result = self.consoles(node).create_camera(fields, subject)
        resp = {"node": node, "result": result, "authenticated": self.verifier is not None}
        self._seen[idempotency_key] = resp
        return resp
