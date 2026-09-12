"""archivesink — the archive as a resource, from the pipeline's side.

A sink bin wrapping splitmuxsink. Segments are written into the SPOOL
under <cam>/e<epoch>/<start>Z.mp4; on fragment-closed the segment is
PROMOTED into the archive resource and its manifest line appended — the
acknowledgement order of М9 Lesson 4. The epoch is a property set by the
worker when it starts the camera; it is in every path this element writes.

    gst-launch-1.0 driverpacksrc uri=driverpack://file/lobby.mp4 ! h264parse \\
        ! archivesink camera=7 epoch=3 spool=/data/spool archive=/data/archive segment-seconds=600
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst  # noqa: E402

from vms.archive import ArchiveResource, segment_path  # noqa: E402

Gst.init(None)


class ArchiveSink(Gst.Bin):
    __gstmetadata__ = ("Archive sink", "Sink/Video", "Segments into the spool, promoted to the archive resource", "edge-vms-course")
    __gproperties__ = {
        "camera": (int, "camera", "camera id", 0, 2 ** 31 - 1, 0, GObject.ParamFlags.READWRITE),
        "epoch": (int, "epoch", "fencing epoch, in every path", 0, 2 ** 31 - 1, 0, GObject.ParamFlags.READWRITE),
        "spool": (str, "spool", "spool root", "/data/spool", GObject.ParamFlags.READWRITE),
        "archive": (str, "archive", "archive resource root", "/data/archive", GObject.ParamFlags.READWRITE),
        "segment-seconds": (int, "segment-seconds", "segment length", 1, 86400, 600, GObject.ParamFlags.READWRITE),
    }

    def __init__(self):
        super().__init__()
        self.props_ = {"camera": 0, "epoch": 0, "spool": "/data/spool", "archive": "/data/archive", "segment-seconds": 600}
        self.mux = Gst.ElementFactory.make("splitmuxsink", "mux")
        self.mux.set_property("muxer-factory", "mp4mux")
        self.mux.set_property("async-finalize", True)
        self.add(self.mux)
        self.add_pad(Gst.GhostPad.new("sink", self.mux.get_request_pad("video")))
        self.mux.connect("format-location", self._location)
        self.resource: ArchiveResource | None = None
        self.promoted = 0

    def do_get_property(self, prop):
        return self.props_[prop.name]

    def do_set_property(self, prop, value):
        self.props_[prop.name] = value
        if prop.name == "segment-seconds":
            self.mux.set_property("max-size-time", int(value) * Gst.SECOND)

    def _location(self, mux, fragment_id):
        if self.resource is None:
            self.resource = ArchiveResource(self.props_["spool"], self.props_["archive"])
        start = datetime.now(timezone.utc).replace(microsecond=0)
        p = segment_path(self.props_["spool"], self.props_["camera"], self.props_["epoch"], start)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def do_handle_message(self, msg):
        """splitmuxsink-fragment-closed: the acknowledgement point."""
        s = msg.get_structure()
        if s and s.get_name() == "splitmuxsink-fragment-closed":
            path = s.get_string("location")
            try:
                self.resource.promote(path)
                self.promoted += 1
            except Exception as e:                     # noqa: BLE001 — the resource is unreachable; the spool keeps it
                Gst.warning(f"archivesink: promote failed for {path}: {e}")
        Gst.Bin.do_handle_message(self, msg)


GObject.type_register(ArchiveSink)
Gst.Element.register(None, "archivesink", Gst.Rank.NONE, ArchiveSink)
