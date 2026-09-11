"""Wiring for the real processes: the environment the jobspecs set, turned
into Clusters over NomadVariables and the object store. Not exercised by the
tests (no Nomad here); everything it wires is."""
from __future__ import annotations

import os

from cluster.objectstore import open_store
from cluster.variables import NomadVariables

from .federation import Cluster, Federation


def federation_from_env(var: str = "CLUSTERS") -> Federation:
    """CLUSTERS=north=http://nomad.north:4646|http://minio.north:9000/x,south=...
    The first entry, or DOMAIN_CLUSTER, is the domain cluster."""
    fed = Federation()
    domain = os.environ.get("DOMAIN_CLUSTER")
    for i, entry in enumerate(filter(None, os.environ.get(var, "").split(","))):
        name, rest = entry.split("=", 1)
        nomad, objects = rest.split("|", 1)
        fed.add(Cluster(name, NomadVariables(addr=nomad), open_store(objects),
                        is_domain_cluster=(name == domain) if domain else i == 0))
    if not fed.clusters:
        raise SystemExit(f"{var} is empty: name at least one cluster")
    return fed
