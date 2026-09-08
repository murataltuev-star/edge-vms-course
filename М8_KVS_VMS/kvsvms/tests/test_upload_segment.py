"""The acknowledged uploader: exit 0 only when the archive holds the segment."""
import os, sys, tempfile, time
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edge import upload_segment as U
from edge.pipeline import build_upload_argv

CFG = {"region": "eu-central-1", "retention": 24, "segment_seconds": 600, "stream": "cam-01"}


def _segment(age_seconds=1000):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "1757350800-00003.mp4")
    with open(p, "wb") as f:
        f.write(b"\0" * 1024)
    t = time.time() - age_seconds
    os.utime(p, (t, t))
    return p, t


def _frag(ts):
    return {"ProducerTimestamp": datetime.fromtimestamp(ts, tz=timezone.utc), "FragmentLengthInMilliseconds": 2000}


def test_argv_is_offline_with_original_start_time():
    argv = build_upload_argv("/data/spool/cam-01/x.mp4", "cam-01", "eu-central-1", 24, 1757350200.7)
    assert argv[0] == "gst-launch-1.0" and "streaming-type=offline" in argv
    assert "file-start-time=1757350200" in argv and "sync=true" not in argv   # no pacing for backlog


def test_span_is_close_time_minus_duration():
    p, mtime = _segment()
    start, end = U.segment_span(p, fallback_seconds=600, now=time.time())
    assert abs(end - mtime) < 1 and abs((end - start) - 600) < 1     # no ffprobe: fallback length


def test_ack_only_when_a_fragment_lands_in_the_span():
    p, mtime = _segment()
    calls = []
    run_ok = lambda argv: (calls.append(argv), type("R", (), {"returncode": 0}))[1]
    # 1. pipeline ok, fragment present -> 0
    rc = U.upload(p, run=run_ok, list_fragments=lambda s, e: [_frag(mtime - 300)], cfg=CFG)
    assert rc == 0 and calls[0][0] == "gst-launch-1.0"
    # 2. pipeline ok, archive empty -> 1 (the write returned; the far side did not)
    assert U.upload(p, run=run_ok, list_fragments=lambda s, e: [], cfg=CFG) == 1
    # 3. fragment outside the span (someone else's footage) -> 1
    assert U.upload(p, run=run_ok, list_fragments=lambda s, e: [_frag(mtime - 5000)], cfg=CFG) == 1
    # 4. pipeline failed -> 1, and the archive is never asked
    asked = []
    run_bad = lambda argv: type("R", (), {"returncode": 1})
    assert U.upload(p, run=run_bad, list_fragments=lambda s, e: asked.append(1) or [], cfg=CFG) == 1
    assert asked == []
