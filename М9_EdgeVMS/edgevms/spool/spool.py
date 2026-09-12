#!/usr/bin/env python3
"""Lesson 4, Steps 6 and 9 — the spool, its uploader, and the two numbers.

    capture -> splitmuxsink -> /data/spool/<camera>/<timestamp>.mp4
                                        |
                                uploader (this file, separate process)
                                        |  on acknowledgement: delete
                                        v
                                       KVS

Four properties, each a decision: delete on acknowledgement never on send;
a bound and a policy for reaching it that the appliance SAYS it applied;
oldest first, stop at the first failure; rate-limited catch-up.

As the uploader:   python3 spool.py --root /data/spool --max-bytes 300000000000 \
                      --policy drop-oldest --budget 2 --tick 5 --upload-cmd "aws-kvs-put"
`--upload-cmd` receives the segment path as its last argument and exits 0 only
once the far side has acknowledged. That exit status is the acknowledgement.

The two numbers are written to --signals (default /run/vms/spool-signals) in
Prometheus text format, where the health check can read them with the uplink
down. М13 gives them an exporter; М9 turns this spool into an archive.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time


class Spool:
    def __init__(self, root, max_bytes, policy="drop-oldest"):
        self.root, self.max_bytes, self.policy = root, max_bytes, policy
        os.makedirs(root, exist_ok=True)
        self.dropped = 0

    def pending(self):
        """Oldest first. The filename carries the timestamp, so sorting is ordering.
        Segments may sit in per-camera subdirectories; the basename orders them."""
        paths = glob.glob(os.path.join(self.root, "*.mp4")) + glob.glob(os.path.join(self.root, "*", "*.mp4"))
        return sorted(paths, key=os.path.basename)

    def used(self):
        return sum(os.path.getsize(p) for p in self.pending())

    def accept(self, name, data):
        """Called when splitmuxsink closes a segment. Returns True if it was kept."""
        if self.used() + len(data) > self.max_bytes:
            if self.policy == "stop-recording":
                return False
            while self.pending() and self.used() + len(data) > self.max_bytes:
                os.remove(self.pending()[0])
                self.dropped += 1
        with open(os.path.join(self.root, name), "wb") as f:
            f.write(data)
        return True

    # -- Step 9: the two numbers ------------------------------------------
    def oldest_seconds(self, now=None):
        p = self.pending()
        if not p:
            return 0.0
        return (now or time.time()) - os.path.getmtime(p[0])

    def signals(self, now=None):
        used = self.used()
        return {
            "spool_oldest_seconds": round(self.oldest_seconds(now), 1),
            "spool_bytes_used": used,
            "spool_bytes_bound": self.max_bytes,
            "spool_segments_dropped_total": self.dropped,
        }


def drain(spool, upload, budget_per_tick=2):
    """Rate-limited catch-up: never more than budget_per_tick per pass."""
    sent = 0
    for path in spool.pending():
        if sent >= budget_per_tick:
            break
        if not upload(path):      # far side did not acknowledge
            break                 # stop on first failure; order is preserved
        os.remove(path)           # delete ON ACKNOWLEDGEMENT, never on send
        sent += 1
    return sent


def write_signals(path, signals):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for k, v in signals.items():
            f.write(f"{k} {v}\n")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="/data/spool")
    ap.add_argument("--max-bytes", type=int, required=True, help="Lesson 1's number: cameras x bitrate x outage")
    ap.add_argument("--policy", choices=["drop-oldest", "stop-recording"], default="drop-oldest")
    ap.add_argument("--budget", type=int, default=2, help="segments per tick during catch-up")
    ap.add_argument("--tick", type=float, default=5.0)
    ap.add_argument("--upload-cmd", required=True, help="command; segment path appended; exit 0 = acknowledged")
    ap.add_argument("--signals", default="/run/vms/spool-signals")
    a = ap.parse_args()

    spool = Spool(a.root, a.max_bytes, a.policy)

    def upload(path: str) -> bool:
        r = subprocess.run(a.upload_cmd.split() + [path], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"upload {os.path.basename(path)} not acknowledged: {r.stderr.strip()[:200]}", file=sys.stderr)
        return r.returncode == 0

    last_dropped = 0
    while True:
        sent = drain(spool, upload, a.budget)
        sig = spool.signals()
        write_signals(a.signals, sig)
        if spool.dropped != last_dropped:            # say which policy did what
            print(f"spool full: policy={a.policy} dropped={spool.dropped - last_dropped} oldest segments", file=sys.stderr)
            last_dropped = spool.dropped
        if sent:
            print(f"drained {sent}; backlog {len(spool.pending())}; oldest {sig['spool_oldest_seconds']}s", file=sys.stderr)
        time.sleep(a.tick)


if __name__ == "__main__":
    sys.exit(main())
