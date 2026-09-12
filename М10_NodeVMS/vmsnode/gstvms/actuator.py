"""The actuator that builds `driverpacksrc ! tee ! archivesink` per camera —
the worker's verb -> pipeline. The tee's second branch is a leaky queue
into a fakesink until М12's gateway subscribes to it."""
from __future__ import annotations

import logging
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from . import archivesink, driverpacksrc  # noqa: E402,F401 — registers the elements

log = logging.getLogger("gstvms")
Gst.init(None)

DESC = ("driverpacksrc uri={uri} name=src ! h264parse ! watchdog timeout={watchdog} ! tee name=t "
        "t. ! queue ! archivesink name=sink camera={cam} epoch={epoch} spool={spool} archive={archive} segment-seconds={seg} "
        "t. ! queue leaky=downstream max-size-buffers=30 ! fakesink sync=false")


class GstActuator:
    def __init__(self, spool: str, archive: str, segment_seconds: int = 600, watchdog_ms: int = 8000):
        self.spool, self.archive, self.seg, self.watchdog = spool, archive, segment_seconds, watchdog_ms
        self.pipelines: dict[int, Gst.Pipeline] = {}
        self.dead: list[int] = []

    def __call__(self, verb: str, cam: dict) -> bool:
        cid = cam["id"]
        if verb in ("stop", "restart") and cid in self.pipelines:
            p = self.pipelines.pop(cid)
            p.send_event(Gst.Event.new_eos())            # lets splitmuxsink finalize the open segment
            p.set_state(Gst.State.NULL)
        if verb == "stop":
            return True
        desc = DESC.format(uri=cam["source"], watchdog=self.watchdog, cam=cid, epoch=cam.get("epoch", 0),
                           spool=self.spool, archive=self.archive, seg=self.seg)
        try:
            p = Gst.parse_launch(desc)
        except Exception as e:                        # noqa: BLE001
            log.error("camera %s: %s", cid, e)
            return False
        bus = p.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda b, m, c=cid: self.dead.append(c))
        if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False
        self.pipelines[cid] = p
        return True

    def pump(self) -> list[int]:
        dead, self.dead = self.dead, []
        for cid in dead:
            p = self.pipelines.pop(cid, None)
            if p:
                p.set_state(Gst.State.NULL)
        return dead

    def stop_all(self) -> None:
        for cid in list(self.pipelines):
            self("stop", {"id": cid})
