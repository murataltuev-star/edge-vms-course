#!/usr/bin/env python3
"""Lesson 2 — dummy workload standing in for the real GStreamer pipeline.
Ticks once a second forever, and exits(1) after CRASH_AFTER seconds if that
env var is set, so the supervisor's crash-and-backoff path can be rehearsed
on demand instead of waiting for a real failure.
Kept in the tree because tests/ and the looper's host mode without GStreamer
still use it (CHILD=camera_sim).
"""
import os
import signal
import sys
import time

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def main():
    crash_after = os.environ.get("CRASH_AFTER")
    crash_after = float(crash_after) if crash_after else None
    start = time.monotonic()
    tick = 0
    print(f"[camera] starting (pid={os.getpid()})", flush=True)
    while not _stop:
        time.sleep(1)
        tick += 1
        print(f"[camera] tick {tick}", flush=True)
        if crash_after is not None and (time.monotonic() - start) >= crash_after:
            print("[camera] simulated crash", flush=True)
            sys.exit(1)
    print("[camera] SIGTERM received, exiting cleanly", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
