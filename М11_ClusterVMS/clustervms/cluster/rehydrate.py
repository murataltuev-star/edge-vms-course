"""Lesson 27 — the six steps, as code.

    1. empty Postgres; migrations run                 (the AppHost did this)
    2. read its own Nomad Variable                    (identity.from_environment)
    3. fetch that object from the CLUSTER's object store
    4. restore it; check the revision against the Variable
    5. request a new epoch                            (epoch.next_epoch)
    6. begin recording into epoch-N+1                 (the AppHost, with EPOCH set)

A Node the directory has never seen comes up `unconfigured` and invents
nothing. A pointer to a missing object is refused, loudly: that is the
publication order broken, and a human must look.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .configio import revision_of
from .identity import Identity

log = logging.getLogger("cluster.rehydrate")


class RestoreRefused(Exception):
    pass


@dataclass
class RestoreResult:
    state: str                   # "restored" | "unconfigured" | "already-configured"
    revision: int = 0
    cameras: int = 0


async def rehydrate(identity: Identity, store, objects) -> RestoreResult:
    if not await store.is_unconfigured():
        # A restart on the same server: the disk is still here. Nothing to do.
        return RestoreResult("already-configured", await store.config_revision())
    if not identity.seen_before:                                          # step 2
        log.warning("%s: the directory has never seen this Node; coming up unconfigured", identity.node)
        return RestoreResult("unconfigured")
    blob = objects.get(identity.config_object)                            # step 3
    if blob is None:
        raise RestoreRefused(f"Variable names {identity.config_object} but the object store has no such object "
                             f"— publication order was broken or the store lost data; refusing to guess")
    if revision_of(blob) != identity.config_revision:                     # step 4
        raise RestoreRefused(f"object {identity.config_object} is revision {revision_of(blob)} but the Variable "
                             f"says {identity.config_revision}; refusing to guess")
    rev = await store.restore_config(blob)
    n = len(await store.cameras())
    log.info("%s: restored revision %d, %d cameras, from %s", identity.node, rev, n, identity.config_object)
    return RestoreResult("restored", rev, n)
