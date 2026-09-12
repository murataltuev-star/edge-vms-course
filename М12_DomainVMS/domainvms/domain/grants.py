"""Lesson 4 — grants are Node-local, carry an expiry, and expiry IS the
revocation mechanism.

A Node stores `subject X may do Y on camera Z until T`. Enforcement is a
local query — no lookup, no token exchange — which is the only way
authorization survives the domain being down. Grants are renewed on the
same upward stream that carries configuration; a Node that cannot renew
lets them lapse. That converts an unbounded revocation window into a
number the product states.

Two lifetimes, and they are not independent: the revocation window is the
SHORTER of the token lifetime and the grant lifetime.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .tokens import KeySet, TokenError, verify

GRANT_LIFETIME = 24 * 3600.0     # Lesson 4 makes students pick and defend it; the product states it


@dataclass(frozen=True)
class Grant:
    subject: str
    capability: str          # "view" | "edit" | "admin"
    camera: int | None       # None = every camera on this Node
    valid_until: float


class NodeGrants:
    """One Node's grants table (М9's `grants`, with valid_until)."""

    def __init__(self, node: str, now=time.time):
        self.node, self.now = node, now
        self.grants: dict[tuple[str, str, int | None], float] = {}

    def grant(self, subject: str, capability: str, camera: int | None, valid_until: float) -> None:
        self.grants[(subject, capability, camera)] = valid_until

    def revoke(self, subject: str) -> None:
        self.grants = {k: v for k, v in self.grants.items() if k[0] != subject}

    def may(self, subject: str, capability: str, camera: int, now: float | None = None) -> bool:
        now = self.now() if now is None else now
        for (s, c, cam), until in self.grants.items():
            if s == subject and c in (capability, "admin") and cam in (camera, None) and now < until:
                return True
        return False

    def renew_from_domain(self, renewals: list[Grant]) -> None:
        """The upward stream carried the domain's current grants for this
        Node: replace, so a grant the domain dropped is not renewed."""
        self.grants = {(g.subject, g.capability, g.camera): g.valid_until for g in renewals}

    def access_ends(self, subject: str, token_exp: float) -> float:
        """State in advance: if a revoke cannot reach this Node, when does
        `subject` lose it? min(token expiry, latest grant expiry)."""
        untils = [u for (s, _, _), u in self.grants.items() if s == subject]
        return min(token_exp, max(untils)) if untils else token_exp


def revocation_window(token_lifetime: float, grant_lifetime: float) -> float:
    """The shorter of the two bounds the window. Most people answer the token."""
    return min(token_lifetime, grant_lifetime)


class NodeAuthoriser:
    """What a Node's console and live endpoint run: signature against the
    key set it holds (from its cluster's Variables, via the domain agent),
    the revocation list it holds, then its own grants. Never a network call."""

    def __init__(self, grants: NodeGrants, keyset: KeySet, revoked: set[str] = frozenset(), now=time.time):
        self.grants, self.keyset, self.revoked, self.now = grants, keyset, revoked, now

    def update_trust(self, keyset: KeySet, revoked: set[str]) -> None:
        self.keyset, self.revoked = keyset, revoked

    def subject(self, token: str) -> str:
        return verify(token, self.keyset, self.revoked, now=self.now())["sub"]

    def authorise(self, token: str, capability: str, camera: int) -> str:
        try:
            subject = self.subject(token)
        except TokenError as e:
            raise PermissionError(f"token refused: {e}")
        if not self.grants.may(subject, capability, camera):
            raise PermissionError(f"{subject} has no {capability} grant on camera {camera} at {self.grants.node}")
        return subject
