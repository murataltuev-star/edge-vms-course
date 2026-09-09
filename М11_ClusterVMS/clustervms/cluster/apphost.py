"""The Node under a scheduler: М10's AppHost plus what М11 adds.

    prologue      identity → migrate → rehydrate → epoch by CAS → lease
    publish()     every second: publish on change, with a floor           (Lesson 3)
    lease()       renew by reading my epoch; fence myself if it moved      (Lesson 4)
    heartbeat()   a timestamp in my Variable, for node_failover_seconds    (Lesson 4)

Everything М10 does — reconcile, pump_buses, report, retention, console —
is inherited unchanged. Only the actuator's gate and the report grow:
a fenced instance starts nothing, and `replicated` joins the conditions.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import signal
import time

from . import identity as identity_mod
from .directory import Directory
from .epoch import Lease, next_epoch
from .objectstore import ObjectStore
from .publish import Publisher
from .rehydrate import RestoreResult, rehydrate
from .reindex import RealFs as ReindexFs, sweep as reindex_sweep
from .variables import Variables

from apphost.apphost import MIGRATIONS, AppHost   # М10's nodevms, on sys.path via cluster/__init__.py

log = logging.getLogger("cluster.apphost")


class ClusterAppHost(AppHost):
    def __init__(self, settings, store, vars_: Variables, objects: ObjectStore,
                 identity: identity_mod.Identity, actuator=None, key=None,
                 lease_ttl: float = 30.0, lease_margin: float = 5.0,
                 publish_floor: float = 5.0, heartbeat_interval: float = 30.0,
                 clock=time.monotonic, wall=time.time):
        super().__init__(settings, store, actuator, key)
        self.vars, self.objects, self.identity = vars_, objects, identity
        self.lease_ttl, self.lease_margin = lease_ttl, lease_margin
        self.heartbeat_interval = heartbeat_interval
        self.clock, self.wall = clock, wall
        self.publisher = Publisher(identity.node, store, vars_, objects, publish_floor, clock)
        self.directory = Directory(vars_, clock=clock)
        self.lease: Lease | None = None
        self.failover: dict = {}
        self.replicated_now = False
        self.restore: RestoreResult | None = None
        self._resume_from_ts: float | None = None

    # -- prologue: the six steps ---------------------------------------------
    async def prologue(self) -> RestoreResult:
        node = self.identity.node
        self.restore = await rehydrate(self.identity, self.store, self.objects)     # steps 2–4
        self.publisher.published_rev = self.identity.config_revision
        hb, _ = self.vars.get(f"nodes/{node}/heartbeat")
        epoch, idx = next_epoch(self.vars, node)                                   # step 5
        self.settings = dataclasses.replace(self.settings, epoch=epoch)            # step 6: EPOCH in the path
        self.actuator.settings = self.settings           # the epoch in every new segment path
        self.lease = Lease(self.vars, node, epoch, self.lease_ttl, self.lease_margin, self.clock)
        fo, _ = self.vars.get(f"nodes/{node}/failover")
        self.failover = {k: float(v) for k, v in (fo or {}).items()}
        if self.restore.state == "restored" and hb and hb.get("ts"):
            self._resume_from_ts = float(hb["ts"])       # the old instance's last sign of life
        log.info("%s: %s (rev %d, %d cameras); epoch %d (ModifyIndex %d)", node, self.restore.state,
                 self.restore.revision, self.restore.cameras, epoch, idx)
        return self.restore

    # -- gates ----------------------------------------------------------------
    def _actuate(self, verb: str, cam: dict) -> bool:
        if verb in ("start", "restart") and self.lease is not None and not self.lease.may_write():
            return False                                 # fenced, or the lease ran out: start nothing
        return super()._actuate(verb, cam)

    async def reconcile_once(self):
        actions = await super().reconcile_once()
        if self._resume_from_ts is not None and any(v == "start" for v, _ in actions):
            secs = max(0.0, self.wall() - self._resume_from_ts)
            self.failover = {"last": secs, "worst": max(secs, self.failover.get("worst", 0.0))}
            self._resume_from_ts = None
            try:
                _, idx = self.vars.get(f"nodes/{self.identity.node}/failover")
                self.vars.put(f"nodes/{self.identity.node}/failover", self.failover, cas=idx)
            except Exception:                            # noqa: BLE001
                log.exception("could not record failover time")
            log.warning("%s: recording resumed %.1fs after the old instance's last heartbeat (worst %.1fs)",
                        self.identity.node, secs, self.failover["worst"])
        return actions

    async def report_once(self) -> None:
        await super().report_once()
        rev = await self.store.config_revision()
        ok, reason = self.publisher.replicated(rev)
        self.replicated_now = ok
        for cam in self.desired.rows:
            await self.store.set_condition(cam["id"], "replicated", ok, reason)

    # -- the added tasks ------------------------------------------------------
    async def publish(self) -> None:
        while not self.stopping.is_set():
            try:
                await self.publisher.publish_once()
            except Exception:                            # noqa: BLE001
                log.exception("publish task failed; retrying")
            await asyncio.sleep(1.0)

    def fence(self, why: str) -> None:
        if not self.recording_allowed:
            return
        log.error("%s: FENCED (%s). Stopping every pipeline; this instance's epoch %d is stale.",
                  self.identity.node, why, self.settings.epoch)
        self.recording_allowed = False
        self.actuator.stop_all()
        self.reconciler.actual.clear()
        self.wake.set()

    async def lease_task(self) -> None:
        interval = max(1.0, (self.lease_ttl - self.lease_margin) / 3)
        while not self.stopping.is_set():
            if self.lease is not None:
                if not self.lease.renew():
                    self.fence("a newer epoch was issued" if self.lease.fenced else "lease expired without renewal")
            await asyncio.sleep(interval)

    async def reindex(self, interval: float = 600.0) -> None:
        """After a restore and periodically: files back into rows — the
        fenced instance's footage, and a returned server's archive."""
        fs = ReindexFs()
        while not self.stopping.is_set():
            try:
                rep = await reindex_sweep(self.store, fs, self.settings.archive_dir, self.settings.epoch,
                                          self.settings.segment_seconds)
                if rep.reindexed:
                    log.info("reindex: %d segments, %d from fenced epochs", rep.reindexed, rep.fenced)
            except Exception:                            # noqa: BLE001
                log.exception("reindex sweep failed")
            await asyncio.sleep(interval)

    async def heartbeat(self) -> None:
        while not self.stopping.is_set():
            try:
                if self.lease is not None and self.lease.may_write():
                    _, idx = self.vars.get(f"nodes/{self.identity.node}/heartbeat")
                    self.vars.put(f"nodes/{self.identity.node}/heartbeat",
                                  {"ts": f"{self.wall():.3f}", "epoch": self.settings.epoch}, cas=idx)
            except Exception:                            # noqa: BLE001
                log.debug("heartbeat write failed (cluster unreachable?)")
            await asyncio.sleep(self.heartbeat_interval)

    # -- lifecycle --------------------------------------------------------------
    async def run(self, serve_console: bool = True) -> None:
        ok = await self.store.migrate(MIGRATIONS)                                   # step 1
        if not ok:
            log.error("running on the previous schema; the box keeps recording")
        await self.prologue()
        listener = None
        try:
            listener = await self.store.listen("cameras", lambda _payload: self.wake.set())
        except Exception:                                                          # noqa: BLE001
            log.exception("LISTEN failed; polling alone (slower, still correct)")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stopping.set)
        tasks = [asyncio.create_task(self.reconcile(), name="reconcile"),
                 asyncio.create_task(self.pump_buses(), name="pump_buses"),
                 asyncio.create_task(self.report(), name="report"),
                 asyncio.create_task(self.retention(), name="retention"),
                 asyncio.create_task(self.publish(), name="publish"),
                 asyncio.create_task(self.lease_task(), name="lease"),
                 asyncio.create_task(self.heartbeat(), name="heartbeat"),
                 asyncio.create_task(self.reindex(), name="reindex")]
        if serve_console:
            import uvicorn
            from console.app import create_app
            from .console import add_cluster_routes
            app = create_app(self)
            add_cluster_routes(app, self)
            server = uvicorn.Server(uvicorn.Config(app, host=self.settings.console_host,
                                                   port=self.settings.console_port, log_level="warning"))
            tasks.append(asyncio.create_task(server.serve(), name="console"))
        await self.stopping.wait()
        log.info("stopping: closing pipelines (each finalizes its open segment)")
        self.actuator.stop_all()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.publisher.publish_once()          # a last publish, best effort
            await self.report_once()
        finally:
            if listener is not None:
                await self.store.pool.release(listener)


async def main() -> None:
    from apphost.config import Settings
    from apphost.secrets import ColumnKey
    from apphost.store import PgStore
    from .configio import ClusterStoreMixin
    from .objectstore import open_store
    from .variables import NomadVariables

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    class ClusterPgStore(ClusterStoreMixin, PgStore):
        pass

    vars_ = NomadVariables()
    ident = identity_mod.from_environment(vars_)
    settings = Settings()
    key = None
    if ident.column_key:
        os.makedirs(os.path.dirname(settings.column_key_file), exist_ok=True)
        with open(settings.column_key_file, "wb") as f:
            f.write(ident.column_key)               # М10's key, delivered by the cluster, not from a backup
        key = ColumnKey(ident.column_key)
    objects = open_store(os.environ.get("OBJECT_STORE_URL", "file:///data/restore"))
    store = await ClusterPgStore.connect(settings.database_url)
    try:
        host = ClusterAppHost(settings, store, vars_, objects, ident, key=key,
                              lease_ttl=float(os.environ.get("LEASE_TTL", "30")),
                              lease_margin=float(os.environ.get("LEASE_MARGIN", "5")),
                              publish_floor=float(os.environ.get("PUBLISH_FLOOR", "5")),
                              heartbeat_interval=float(os.environ.get("HEARTBEAT_INTERVAL", "30")))
        await host.run()
    finally:
        await store.close()
