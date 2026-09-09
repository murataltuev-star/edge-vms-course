"""Lesson 2 — who am I? Never the allocation index. The scheduler delivers
the Node's own Variable through its template as environment; this reads it,
and can also read the Variable directly for the values a template did not
carry (the column key, the heartbeat).

    nodes/<node>          node, config, revision, cameras
    nodes/<node>/pg       password
    nodes/<node>/key      hex            (М10's column key — the debt paid)
    nodes/<node>/epoch    epoch
    nodes/<node>/heartbeat ts, epoch     (for node_failover_seconds)
    nodes/<node>/failover last, worst    (RTO, worst case)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .variables import Variables


@dataclass
class Identity:
    node: str
    config_object: str          # "" when the directory has never seen this Node
    config_revision: int
    camera_ids: list[int]
    column_key: bytes | None

    @property
    def seen_before(self) -> bool:
        return bool(self.config_object)


def from_environment(vars_: Variables | None = None) -> Identity:
    node = os.environ.get("NODE_ID") or os.environ.get("NOMAD_JOB_NAME")
    if not node:
        raise RuntimeError("NODE_ID is not set: a Node must know what it is before it does anything")
    cfg = os.environ.get("CONFIG_OBJECT", "")
    rev = int(os.environ.get("CONFIG_REVISION", "0") or 0)
    cams = [int(c) for c in os.environ.get("CAMERA_IDS", "").split(",") if c.strip()]
    key = None
    if vars_ is not None:
        items, _ = vars_.get(f"nodes/{node}/key")
        if items and items.get("hex"):
            key = bytes.fromhex(items["hex"])
        # the template may be stale by one render; the Variable is authoritative
        items, _ = vars_.get(f"nodes/{node}")
        if items:
            cfg = items.get("config", cfg)
            rev = int(items.get("revision", rev) or 0)
            cams = [int(c) for c in items.get("cameras", "").split(",") if c.strip()] or cams
    return Identity(node, cfg, rev, cams, key)
