"""The domain agent: one small Nomad job per cluster whose only right is to
write domain/* in that cluster's Variables — the way a Node's only right is
to write nodes/<node>/*.

It carries the two things every cluster needs from the domain and nothing
else: the signer's public key set and the revocation list. When the domain
is unreachable it stops updating; Nodes keep verifying with the keys they
have, issued tokens run to expiry, nobody new logs in — the bounded outage
the services table promises, with the mechanism named.
"""
from __future__ import annotations

import time

from cluster.variables import Variables

from .federation import Unreachable
from .tokens import KeySet, RevocationList

KEYS_PATH, REVOKED_PATH = "domain/keys", "domain/revoked"


class DomainPublisher:
    """The signer's side: writes the key set and the revocation list into
    the DOMAIN cluster's Variables, where agents read them."""

    def __init__(self, domain_vars: Variables):
        self.vars = domain_vars

    def publish_keys(self, ks: KeySet) -> None:
        _, idx = self.vars.get(KEYS_PATH)
        self.vars.put(KEYS_PATH, ks.to_items(), cas=idx)

    def publish_revoked(self, rl: RevocationList) -> None:
        _, idx = self.vars.get(REVOKED_PATH)
        self.vars.put(REVOKED_PATH, rl.to_items(), cas=idx)


class DomainAgent:
    def __init__(self, cluster: str, domain_vars: Variables, cluster_vars: Variables, now=time.time):
        self.cluster, self.domain_vars, self.cluster_vars, self.now = cluster, domain_vars, cluster_vars, now
        self.last_synced: float | None = None
        self.syncs = 0

    def sync(self) -> bool:
        """One pass. False (and nothing written) if the domain did not answer."""
        try:
            keys, _ = self.domain_vars.get(KEYS_PATH)
            revoked, _ = self.domain_vars.get(REVOKED_PATH)
        except Unreachable:
            return False
        for path, items in ((KEYS_PATH, keys), (REVOKED_PATH, revoked)):
            if items is None:
                continue
            have, idx = self.cluster_vars.get(path)
            if have != items:
                self.cluster_vars.put(path, items, cas=idx)
        self.last_synced = self.now()
        self.syncs += 1
        return True


class NodeTrust:
    """What a Node reads from ITS OWN cluster's Variables — never from the
    domain — to verify tokens offline."""

    def __init__(self, cluster_vars: Variables):
        self.vars = cluster_vars

    def keyset(self) -> KeySet | None:
        items, _ = self.vars.get(KEYS_PATH)
        return KeySet.from_items(items) if items else None

    def revoked(self) -> set[str]:
        items, _ = self.vars.get(REVOKED_PATH)
        return RevocationList.from_items(items).jtis


def main() -> None:
    """python3 -m domain.agent — one per cluster."""
    import os
    import signal
    import threading

    from cluster.variables import NomadVariables

    cluster = os.environ.get("CLUSTER", os.environ.get("NOMAD_REGION", "local"))
    agent = DomainAgent(cluster, NomadVariables(addr=os.environ["DOMAIN_NOMAD_ADDR"]),
                        NomadVariables(addr=os.environ.get("NOMAD_ADDR")))
    interval = float(os.environ.get("SYNC_INTERVAL", "30"))
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    while not stop.is_set():
        try:
            ok = agent.sync()
        except Exception:                                    # noqa: BLE001 — the domain is unreachable; keep the last set
            ok = False
        if not ok:
            print(f"{cluster}: domain unreachable; keeping the key set from {agent.last_synced}", flush=True)
        stop.wait(interval)


if __name__ == "__main__":
    main()
