"""python3 -m cluster worker | controller | resource — the three jobs.

    NOMAD_ADDR, NOMAD_TOKEN            the task's own workload identity (Variables)
    OBJECTS=s3+http://minio:9000/vms   the object store (heartbeats, snapshots); file:///path on a bench
    NOMAD_ALLOC_INDEX                  worker: the slot to claim; NOMAD_NODE_NAME the server; NOMAD_META_labels
    SPOOL, ARCHIVE                     worker and resource: the same disks on the same server
    RESOURCE_URL                       resource: how the console reaches this server's manifests
    CAPACITY                           worker: cameras it can carry on this server
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

import cluster  # noqa: F401  — puts М10's vmsnode on sys.path

from cluster.objectstore import open_store
from cluster.variables import NomadVariables

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
stop = threading.Event()
for s in (signal.SIGTERM, signal.SIGINT):
    signal.signal(s, lambda *_: stop.set())
objects = open_store(os.environ.get("OBJECTS", "file:///data/objects"))
spool, archive = os.environ.get("SPOOL", "/data/spool"), os.environ.get("ARCHIVE", "/data/archive")


def worker() -> None:
    from vms.archive import ArchiveResource
    from cluster.worker import ClusterWorker
    try:
        from gstvms.actuator import GstActuator
        act = GstActuator(spool, archive, int(os.environ.get("SEGMENT_SECONDS", "600")))
    except ImportError:
        logging.warning("no GStreamer: the fake actuator records nothing"); act = None
    res = ArchiveResource(spool, archive)
    for p in res.closed_in_spool(grace_seconds=30, now=time.time()):
        res.promote(p)
    w = ClusterWorker(NomadVariables(), objects, act)
    logging.info("worker %s on %s (alloc %s) claimed its slot; labels %s", w.name, w.server, w.alloc, w.labels)
    w.run(stop=stop)                              # SIGTERM from Nomad → release_slot(): scale-in, not a crash


def controller() -> None:
    from cluster.console import serve
    from cluster.controller import ClusterController
    ctl = ClusterController(NomadVariables(), objects, capacity=int(os.environ.get("CAPACITY", "50")),
                            cluster=os.environ.get("CLUSTER", "cluster-a"))
    srv = serve(ctl, os.environ.get("CONSOLE_HOST", "0.0.0.0"), int(os.environ.get("CONSOLE_PORT", "8080")))
    while not stop.is_set():
        try:
            ctl.ensure_placed(); ctl.redistribute(); ctl.publish_snapshot()
        except Exception:                         # noqa: BLE001
            logging.exception("placement pass failed")
        stop.wait(5)
    srv.shutdown()


def resource() -> None:
    from vms.archive import ArchiveResource
    from cluster.resource import ResourceHeartbeat, ResourcePolicy, serve
    res = ArchiveResource(spool, archive)
    server = os.environ.get("NOMAD_NODE_NAME") or os.uname().nodename
    url = os.environ.get("RESOURCE_URL", f"http://{server}:8090")
    srv = serve(res, "0.0.0.0", int(os.environ.get("RESOURCE_PORT", "8090")))
    hb, policy = ResourceHeartbeat(res, objects, server, url), ResourcePolicy(res, NomadVariables())
    last_policy = 0.0
    while not stop.is_set():
        try:
            hb.once()
            if time.time() - last_policy >= 600:
                logging.info("policy: %s", policy.once()); last_policy = time.time()
        except Exception:                         # noqa: BLE001
            logging.exception("resource pass failed")
        stop.wait(10)
    srv.shutdown()


if __name__ == "__main__":
    {"worker": worker, "controller": controller, "resource": resource}[sys.argv[1]]()
