# Lesson 2 — `driverpacksrc`

**Module:** ServerVMS — the platform's shape on one server (Module 10)
**You will build:** a GStreamer source element that plays a file as if it were a camera — `driverpack://file/<name>` — looping, paced by its own timestamps, with PTS rebased across the loop so the pipeline's running time never goes backwards.
**Time:** ~120 minutes.

## Why this lesson exists

The course has stood in for cameras since М8: `camera_sim.py`, then a looped file behind `filesrc ! qtdemux`. Those were stand-ins *outside* the pipeline. This lesson makes the stand-in an **element**, and the difference is the whole point: everything downstream of the source — the parser, the watchdog, the tee, the archive sink — is the product's pipeline, and it does not change when the real DriverPack arrives. The URI scheme says so: `driverpack://file/lobby.mp4` is this course's; `driverpack://hikvision/10.0.0.7` names the element the product ships, and this one refuses it by name.

It is also where the one non-mechanical part of any source lives. A vendor SDK hands over frames with *its* clock; a file hands over frames with the clock they were recorded at; a pipeline needs one clock that only goes forward. Rebasing timestamps is the work a real DriverPack element does, and it is the work that breaks `splitmuxsink` and playback when it is wrong — so it is built first and tested for an hour.

> **What you can verify without hardware.** The URI resolution and its refusals run anywhere (`test_lesson2_driverpacksrc.py`). The element itself needs GStreamer with PyGObject — `python3-gi`, `gstreamer1.0-plugins-good` and `-bad` — which is М9's bench; the test that constructs it prints *skipped* where `gi` is absent. The hour-long run is yours.

## Prerequisites

- **М8 Lesson 4** — GStreamer fundamentals: elements, pads, caps, the bus, `gst-launch-1.0`.
- **М9 Lesson 7** — fifty pipelines in one process, and the per-frame rule.
- **Lesson 1** — where the source's URI comes from (a camera row in the config store).

## Learning objectives

1. Write a GStreamer element in Python: a `Gst.Bin` with ghost pads, properties, and registration as a plugin.
2. Resolve `driverpack://file/<name>` safely and refuse everything else with a message that names the real thing.
3. Loop a file without the pipeline's running time going backwards.
4. Explain what `identity sync=true` does for a file and does not do for a camera.
5. Restate the per-frame rule for an element author.

---

## Step 1 — A bin with one pad

A GStreamer element in Python is a `Gst.Bin` subclass: inner elements, links, a ghost pad exposing one of them, properties declared in `__gproperties__`, and a registration so `gst-launch-1.0` can find it by name.

```python
class DriverPackSrc(Gst.Bin):
    __gstmetadata__ = ("DriverPack source", "Source/Video", "Plays a media file as if it were a camera", "edge-vms-course")
    __gproperties__ = {"uri": (str, "uri", "driverpack://file/<name>", "", GObject.ParamFlags.READWRITE)}

    def __init__(self):
        filesrc ! qtdemux ! h264parse ! identity sync=true      # inside the bin
        self.add_pad(Gst.GhostPad.new("src", self.pace.get_static_pad("src")))

GObject.type_register(DriverPackSrc)
Gst.Element.register(None, "driverpacksrc", Gst.Rank.NONE, DriverPackSrc)
```

`qtdemux` adds its pads late, when it has read the file's headers, so the bin links the demuxer's video pad to the parser in a `pad-added` handler. `identity sync=true` is what makes a file behave like a live source: it holds each buffer until the pipeline clock reaches its timestamp, so a ten-minute clip takes ten minutes. A camera does not need it — it paces itself — and the real DriverPack element does not carry it.

## Step 2 — The URI, and the refusal

```
driverpack://file/lobby.mp4          -> <MEDIA_DIR>/lobby.mp4
rtsp://10.0.0.7/s                    -> ValueError: not a driverpack URI
driverpack://hikvision/10.0.0.7      -> ValueError: driverpack://hikvision/… names a vendor driver;
                                        this course ships only driverpack://file/<name>
driverpack://file/../etc/passwd      -> ValueError: bad media name
```

The refusal is not a stub's apology; it is the boundary. A camera row's `source` is a URI, the worker hands it to this element, and the element decides what it can open. When DriverPack ships, `driverpack://<vendor>/<host>` resolves inside *its* element and the row, the worker and the pipeline behind the source do not change. The path check is the other half: `MEDIA_DIR` is a directory of files an operator put there, and the element must not be a way to read anything else.

## Step 3 — Timestamps, and the loop

A file's buffers carry PTS from zero to its length. Loop it by seeking to zero on EOS and the second pass carries PTS from zero again — and `splitmuxsink`, which cuts segments by running time, sees time go backwards and either refuses the buffer or opens a segment that ends before it began. So the element keeps an **offset**:

```python
def _rebase(self, pad, info):            # a buffer probe on the bin's output
    buf.pts += self.offset; buf.dts = buf.pts; self.last_pts = buf.pts

def _on_event(self, pad, info):          # an event probe: EOS never leaves the bin
    if ev.type == Gst.EventType.EOS:
        self.offset = self.last_pts + one frame
        self.src.seek_simple(TIME, FLUSH | KEY_UNIT, 0)
        return Gst.PadProbeReturn.DROP
```

Two probes on the ghost pad's target: one rewrites every buffer's timestamps by the accumulated offset, one swallows EOS, advances the offset past the last timestamp and seeks the file back to its start. Downstream never sees an EOS and never sees time decrease. That is the entire rebasing logic, and it is also exactly what a real element does with a camera whose clock jumps — an SDK that resets its timestamps on reconnect, a camera whose NTP correction moves time backwards — which is why it is here rather than in a later "hardening" lesson.

Run it for an hour:

```bash
GST_DEBUG=driverpacksrc:5 gst-launch-1.0 driverpacksrc uri=driverpack://file/lobby.mp4 ! h264parse ! fakesink -v
```

and watch PTS: through every loop boundary it increases by one frame interval and never repeats. A clip of any length, looped any number of times, is one monotonic stream.

## Step 4 — The per-frame rule, for an element author

М9 Lesson 7 measured why Python must not touch buffers: a per-buffer callback in Python crosses the GIL from a streaming thread, and fifty cameras at 25 fps is 1,250 crossings a second. This element *has* a per-buffer probe — `_rebase` — and it runs in Python. That is acceptable in the course for the reason М9 Lesson 9 gave: the prototype's job is the shape, and the shape is right. It is not acceptable in the product, where the same probe is ten lines of C in DriverPack's element. The rule for an element author is: everything that runs per buffer is the worker's language; everything that runs per event, per segment or per state change may be the controller's.

**Deliverable:** `gst-launch-1.0 driverpacksrc uri=driverpack://file/lobby.mp4 ! h264parse ! fakesink -v` running for an hour with monotonic PTS across every loop; the refusal of `driverpack://hikvision/…` with the message above; and `test_lesson2_driverpacksrc.py` green.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `no element "driverpacksrc"` | The module registering it was never imported. `gst-launch` needs it on `GST_PLUGIN_PATH` as a Python plugin, or the worker imports `gstvms.driverpacksrc` before `parse_launch`. |
| The pipeline runs at full speed, not real time | `identity sync=true` is missing, or the pipeline has no clock (`fakesink sync=false` downstream is fine; the identity is what paces). |
| `splitmuxsink` (Lesson 3) writes one segment then nothing | PTS went backwards at the loop: the offset is not applied, or DTS was left behind. Both go forward together. |
| A gap of one frame at every loop | The offset adds one frame interval on purpose; a seam of one frame is the honest cost. Set it from the stream's framerate rather than 1/25 if it matters. |
| The demuxer's pad never links | The file is not H.264 in MP4. This element is deliberately narrow; a second demuxer is a second element. |

## Recap

- The stand-in is an element now, and everything downstream of it is the product's.
- `driverpack://file/<name>` is this course's scheme; the vendor form is refused by name, and the path is checked.
- Looping is a seek plus an offset; EOS never leaves the bin; PTS and DTS go forward together.
- `identity sync=true` paces a file; a camera paces itself.
- Per buffer belongs to the worker's language; the prototype breaks the rule on purpose and says so.

## Exercises

1. Make the offset add nothing at the loop (set the frame interval to zero) and run for ten minutes into `splitmuxsink`. Report what the segment boundaries look like.
2. Add a `speed` property (0.5×, 2×) by scaling PTS in the probe. Then say what a camera would have to send for that to be a real feature.
3. Write the caps negotiation for an H.265 file: which element changes, and does the URI?
4. Measure the probe's cost: fifty instances of this element at 25 fps into `fakesink`, `top -H`. Compare with fifty `filesrc ! qtdemux ! h264parse ! fakesink` and attribute the difference.
5. Sketch `driverpack://onvif/<host>` as an element: what does it need from the platform (credentials), and where does it get them?

## Where this is going

The source is real. [**Lesson 3**](03-archivesink-and-the-archive-as-a-resource.md) builds where it goes: `archivesink`, the spool's discipline applied to an archive that is a resource — segments that appear whole or not at all, promoted with the epoch in the path, and a manifest instead of an index table.
