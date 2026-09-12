"""The VMS's schema, as items in the platform's config store.

    vms/cameras/<id>      id, name, source, enabled, retention_days, revision   (the controller writes)
    vms/workers/<worker>  units, rev                                             (the controller writes)
    vms/placement/<id>    worker, reason, at, rev                                (the controller writes)
    vms/epoch/<id>        epoch                                                  (a worker takes, by CAS)
    vms/next_id           n                                                      (the controller)

A camera row is small, rare and must be consistent: raft's shape. The
operator-owned columns are name, source, enabled, retention_days; the
controller owns revision; nothing here is controller-derived status —
that is in the worker's heartbeat, and only there.
"""
from __future__ import annotations

OPERATOR_FIELDS = ("name", "source", "enabled", "retention_days", "priority")
FORBIDDEN_FIELDS = ("worker", "placement", "epoch", "revision", "observed_revision", "phase", "id")


def row(items: dict) -> dict:
    return {"id": int(items["id"]), "name": items.get("name", ""), "source": items.get("source", ""),
            "enabled": items.get("enabled", "true") == "true", "retention_days": int(items.get("retention_days", 30)),
            "priority": int(items.get("priority", 100)), "revision": int(items.get("revision", 1))}


def items(row_: dict) -> dict:
    return {"id": row_["id"], "name": row_["name"], "source": row_["source"],
            "enabled": "true" if row_["enabled"] else "false", "retention_days": row_["retention_days"],
            "priority": row_.get("priority", 100), "revision": row_["revision"]}
