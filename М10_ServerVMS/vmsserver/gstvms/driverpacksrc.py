"""driverpacksrc — a source element whose URI names what the real DriverPack
will open. In this course: driverpack://file/<name> plays <MEDIA_DIR>/<name>
in a loop, paced by its own timestamps, with PTS rebased across each loop
so the pipeline's running time never goes backwards. That rebasing is the
one non-mechanical part of any source element — the part a real DriverPack
does with a vendor SDK's clock — which is why it is built and tested here.

    gst-launch-1.0 driverpacksrc uri=driverpack://file/lobby.mp4 ! h264parse ! fakesink -v
"""
from __future__ import annotations

import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst  # noqa: E402

Gst.init(None)


from .uri import resolve  # noqa: E402


class DriverPackSrc(Gst.Bin):
    __gstmetadata__ = ("DriverPack source", "Source/Video", "Plays a media file as if it were a camera", "edge-vms-course")
    __gproperties__ = {"uri": (str, "uri", "driverpack://file/<name>", "", GObject.ParamFlags.READWRITE)}

    def __init__(self):
        super().__init__()
        self.uri = ""
        self.src = Gst.ElementFactory.make("filesrc", "file")
        self.demux = Gst.ElementFactory.make("qtdemux", "demux")
        self.parse = Gst.ElementFactory.make("h264parse", "parse")
        self.pace = Gst.ElementFactory.make("identity", "pace")
        self.pace.set_property("sync", True)              # a file has no clock; the pipeline's is used
        for e in (self.src, self.demux, self.parse, self.pace):
            self.add(e)
        self.src.link(self.demux)
        self.demux.connect("pad-added", self._on_pad)
        self.parse.link(self.pace)
        self.add_pad(Gst.GhostPad.new("src", self.pace.get_static_pad("src")))
        self.offset = 0                                    # the rebasing: accumulated across loops
        self.pace.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._rebase)
        self.pace.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_event)
        self.last_pts = 0

    def do_get_property(self, prop):
        return self.uri

    def do_set_property(self, prop, value):
        self.uri = value
        self.src.set_property("location", resolve(value))

    def _on_pad(self, demux, pad):
        if pad.get_current_caps().to_string().startswith("video/x-h264"):
            pad.link(self.parse.get_static_pad("sink"))

    def _rebase(self, pad, info):
        buf = info.get_buffer()
        if buf.pts != Gst.CLOCK_TIME_NONE:
            buf.pts += self.offset
            buf.dts = buf.pts
            self.last_pts = buf.pts
        return Gst.PadProbeReturn.OK

    def _on_event(self, pad, info):
        ev = info.get_event()
        if ev.type == Gst.EventType.EOS:
            # loop: seek to zero and carry the offset forward so PTS keeps increasing
            self.offset = self.last_pts + Gst.SECOND // 25
            self.src.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
            return Gst.PadProbeReturn.DROP
        return Gst.PadProbeReturn.OK


GObject.type_register(DriverPackSrc)
Gst.Element.register(None, "driverpacksrc", Gst.Rank.NONE, DriverPackSrc)
