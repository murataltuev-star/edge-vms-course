"""Lesson 5 — the cluster's questions, on the same console as М10's.

    GET /cluster/node               who am I, which epoch, lease, replicated, failover
    GET /cluster/directory          every Node's holdings, one scan
    GET /cluster/where/{camera_id}  where is camera 7
    GET /metrics                    М10's, plus node_failover_seconds and node_epoch_conflicts
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from . import metrics as cluster_metrics


def add_cluster_routes(app: FastAPI, host) -> None:
    # Replace М10's /metrics with one that appends the cluster's two numbers.
    base_metrics = None
    for r in list(app.routes):
        if getattr(r, "path", None) == "/metrics":
            base_metrics = r.endpoint
            app.routes.remove(r)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        base = await base_metrics() if base_metrics else ""
        return base + cluster_metrics.render(host)

    @app.get("/cluster/node")
    async def node():
        return {"node": host.identity.node, "epoch": host.settings.epoch,
                "restore": host.restore.state if host.restore else None,
                "lease_seconds_left": host.lease.seconds_left() if host.lease else None,
                "fenced": host.lease.fenced if host.lease else None,
                "replicated": host.replicated_now, "failover": host.failover}

    @app.get("/cluster/directory")
    async def directory():
        return host.directory.scan()

    @app.get("/cluster/where/{camera_id}")
    async def where(camera_id: int):
        node = host.directory.where(camera_id)
        if node is None:
            raise HTTPException(404, f"camera {camera_id} is on no Node this cluster knows")
        return {"camera": camera_id, "node": node, "here": node == host.identity.node}
