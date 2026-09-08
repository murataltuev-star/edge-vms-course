#!/usr/bin/env python3
"""edge/looper.py — Lessons 5, 8 and 10: the supervisor.

Starts the child, restarts it with exponential backoff on failure (1 s -> 30 s
cap), restarts it immediately on a clean exit (a finite clip reaching EOS is
the NORMAL case, and the seam is a real gap the timeline renders), and shuts
it down cleanly on SIGINT/SIGTERM. Docker mode wraps the same argv in
`docker run` and removes any stale container by name before every launch.

    CHILD=camera_sim            Lesson 5's dummy workload (no GStreamer needed)
    KVS_DOCKER_IMAGE=...        run the pipeline inside that image (Lesson 8)
    VMS_SPOOL_DIR=/data/spool   М9: write segments to the spool instead of kvssink
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edge.pipeline import build_pipeline_argv, build_spool_pipeline_argv  # noqa: E402

try:
    from server.config import (AWS_REGION, CLIP_PATH, DOCKER_IMAGE, RETENTION_HOURS,  # noqa: E402
                               SEGMENT_SECONDS, SPOOL_DIR, STREAM_NAME)
except KeyError:                      # no AWS_REGION: CHILD=camera_sim still works
    AWS_REGION, CLIP_PATH, DOCKER_IMAGE = "", "", os.environ.get("KVS_DOCKER_IMAGE", "")
    RETENTION_HOURS, STREAM_NAME = 24, os.environ.get("KVS_STREAM_NAME", "cam-01")
    SPOOL_DIR, SEGMENT_SECONDS = os.environ.get("VMS_SPOOL_DIR", ""), int(os.environ.get("SEGMENT_SECONDS", "600"))

CHILD = os.environ.get("CHILD", "pipeline")          # "pipeline" | "camera_sim"
CHILD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_sim.py")
CONTAINER_NAME = f"kvs-vms-edge-{STREAM_NAME}"
BACKOFF_START = 1.0
BACKOFF_CAP = 30.0

_shutting_down = False
_current_proc: subprocess.Popen | None = None


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}  {msg}", flush=True)


def _request_shutdown(signum, frame):
    global _shutting_down
    _log(f"received {signal.Signals(signum).name}, shutting down")
    _shutting_down = True
    if _current_proc is not None and _current_proc.poll() is None:
        _current_proc.terminate()               # SIGTERM to the child; the handler stays tiny


signal.signal(signal.SIGINT, _request_shutdown)
signal.signal(signal.SIGTERM, _request_shutdown)


def _child_argv():
    if CHILD == "camera_sim":
        return [sys.executable, CHILD_SCRIPT]
    if SPOOL_DIR:
        os.makedirs(os.path.join(SPOOL_DIR, STREAM_NAME), exist_ok=True)
        return build_spool_pipeline_argv(CLIP_PATH, os.path.join(SPOOL_DIR, STREAM_NAME),
                                         SEGMENT_SECONDS, time.time())
    return build_pipeline_argv(CLIP_PATH, STREAM_NAME, AWS_REGION, RETENTION_HOURS)


def _build_argv():
    inner = _child_argv()
    if not DOCKER_IMAGE or CHILD == "camera_sim":
        return inner
    # Lesson 8, Step 5: credentials by NAME only — the value never enters an argv.
    argv = ["docker", "run", "--name", CONTAINER_NAME]
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION"):
        if var in os.environ:
            argv += ["-e", var]
    argv += ["-v", f"{os.path.abspath(CLIP_PATH)}:{CLIP_PATH}:ro"]
    if SPOOL_DIR:
        argv += ["-v", f"{SPOOL_DIR}:{SPOOL_DIR}"]
    argv.append(DOCKER_IMAGE)
    # docker/kvssink's ENTRYPOINT is already `gst-launch-1.0 -q`; pass the rest.
    return argv + (inner[2:] if inner[:2] == ["gst-launch-1.0", "-q"] else inner)


def _remove_stale_container():
    """`docker run --name` fails outright if a container of that name still
    exists, running or merely stopped — an orphan left by a SIGKILL'd client
    would otherwise fail every subsequent restart forever. Runs before every
    iteration, and once more on shutdown."""
    if DOCKER_IMAGE and CHILD != "camera_sim":
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_child_once():
    global _current_proc
    _remove_stale_container()
    argv = _build_argv()                            # a list, never shell=True
    _current_proc = subprocess.Popen(argv)
    if _shutting_down:                              # requested between spawn and here
        _current_proc.terminate()
    started = time.monotonic()
    _current_proc.wait()
    duration = time.monotonic() - started
    returncode = _current_proc.returncode
    _current_proc = None
    return returncode, duration


def main():
    backoff = BACKOFF_START
    loop_num = 0
    try:
        while not _shutting_down:
            loop_num += 1
            label = f" (docker image {DOCKER_IMAGE})" if DOCKER_IMAGE and CHILD != "camera_sim" else ""
            _log(f"loop {loop_num} started{label}")
            returncode, duration = run_child_once()
            if _shutting_down:
                _log(f"loop {loop_num} stopped after {duration:.1f}s (shutdown)")
                break
            if returncode == 0:
                _log(f"loop {loop_num} exited cleanly after {duration:.1f}s, restarting")
                backoff = BACKOFF_START
                continue
            if returncode < 0:
                _log(f"loop {loop_num} killed by signal {-returncode} after {duration:.1f}s")
            else:
                _log(f"loop {loop_num} failed (exit {returncode}) after {duration:.1f}s")
            _log(f"backing off {backoff:.0f}s before retry")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP)
    finally:
        _remove_stale_container()
        _log("stopped")


if __name__ == "__main__":
    main()
