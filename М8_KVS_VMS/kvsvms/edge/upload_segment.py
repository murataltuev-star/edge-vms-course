#!/usr/bin/env python3
"""edge/upload_segment.py — the piece М9 Lesson 19 needed and М8 never wrote.

    vms-upload-segment /data/spool/cam-01/1757350800-00003.mp4

Re-publishes one CLOSED spool segment into KVS and exits 0 ONLY when the far
side has acknowledged it. The exit status is the acknowledgement the spool's
delete-on-ack rule waits for (М9 edgevms/spool/spool.py).

Two things make this honest rather than "the write returned":

  1. The pipeline runs kvssink in streaming-type=offline with the segment's
     ORIGINAL start time (file-start-time), so the archive's timeline shows
     footage when it was captured, not when the uplink came back — and
     offline mode makes kvssink wait for persistence before EOS completes.
  2. It then asks the archive: ListFragments over the segment's own time
     range. No fragments, no acknowledgement, exit 1, the file stays.

The start time is the segment's mtime (its close) minus its duration, read
with ffprobe when present; a pipeline restart never resumes a file, so a
segment's mtime is a reliable close time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edge.pipeline import build_upload_argv  # noqa: E402


def segment_duration(path: str, fallback: float) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30).stdout.strip()
        d = float(out)
        return d if d > 0 else fallback
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        return fallback


def segment_span(path: str, fallback_seconds: float, now: float | None = None) -> tuple[float, float]:
    """(start, end) in epoch seconds. end = close time = mtime; start = end - duration.
    Clamped so a clock oddity can never produce a start in the future."""
    end = os.path.getmtime(path)
    start = end - segment_duration(path, fallback_seconds)
    limit = (now or time.time())
    return min(start, limit), min(end, limit)


def acknowledged(fragments: list, start: float, end: float, to_epoch) -> bool:
    """The far side has it if at least one fragment's producer timestamp
    falls inside the segment's span (with a little slack for the muxer)."""
    slack = 2.0
    for f in fragments:
        ts = to_epoch(f["ProducerTimestamp"])
        if start - slack <= ts <= end + slack:
            return True
    return False


def upload(path: str, run=subprocess.run, list_fragments=None, cfg=None, now=None) -> int:
    """Returns the exit status: 0 acknowledged, 1 not. Injected pieces make
    the decision testable without gst-launch or AWS."""
    if cfg is None:
        from server.config import AWS_REGION, RETENTION_HOURS, SEGMENT_SECONDS, STREAM_NAME
        cfg = {"region": AWS_REGION, "retention": RETENTION_HOURS,
               "segment_seconds": SEGMENT_SECONDS, "stream": STREAM_NAME}
    start, end = segment_span(path, cfg["segment_seconds"], now)
    argv = build_upload_argv(path, cfg["stream"], cfg["region"], cfg["retention"], start)
    r = run(argv)
    if r.returncode != 0:
        print(f"upload_segment: gst-launch exited {r.returncode} for {os.path.basename(path)}", file=sys.stderr)
        return 1

    if list_fragments is None:
        from server.fragments import list_all_fragments
        from server.kvs import archived_client
        from server.models import from_epoch, to_epoch
        client = archived_client("LIST_FRAGMENTS")
        frags = list_all_fragments(client, cfg["stream"], from_epoch(start - 5), from_epoch(end + 5))
    else:
        from server.models import to_epoch
        frags = list_fragments(start - 5, end + 5)
    if acknowledged(frags, start, end, to_epoch):
        return 0
    print(f"upload_segment: pipeline finished but the archive holds no fragment for "
          f"{os.path.basename(path)} [{start:.0f}..{end:.0f}]; not acknowledged", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: upload_segment.py <segment.mp4>")
    sys.exit(upload(sys.argv[1]))
