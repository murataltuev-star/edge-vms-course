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
        self.posted: list[tuple[int, str, dict]] = []   # what elements posted on the bus: (camera, kind, fields)

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
        bus.connect("message::element", lambda b, m, c=cid: self._posted(c, m))     # motion, person, ...: an element saw something
        if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False
        self.pipelines[cid] = p
        return True

    def _posted(self, cid: int, msg) -> None:
        st = msg.get_structure()
        if st is None or st.get_name() in ("GstBinForwarded", "splitmuxsink-fragment-opened", "splitmuxsink-fragment-closed"):
            return                                       # plumbing, not an observation
        fields = {}
        for i in range(st.n_fields()):
            name = st.nth_field_name(i)
            v = st.get_value(name)
            if isinstance(v, (int, float, str, bool)):
                fields[name] = v
        self.posted.append((cid, st.get_name(), fields))

    def pump(self) -> tuple[list[int], list[tuple[int, str, dict]]]:
        """(dead cameras, posted observations) since the last pump. The element
        never knows about buckets or epochs: it posts what it saw; the worker,
        which holds the epoch, turns it into a line."""
        dead, self.dead = self.dead, []
        posted, self.posted = self.posted, []
        for cid in dead:
            p = self.pipelines.pop(cid, None)
            if p:
                p.set_state(Gst.State.NULL)
        return dead, posted

    def stop_all(self) -> None:
        for cid in list(self.pipelines):
            self("stop", {"id": cid})
