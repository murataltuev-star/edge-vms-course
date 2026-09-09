"""Lesson 4, Step 3 — the lease state machine, on a MONOTONIC clock.

  holder:       may write while  now - last_renewal < TTL - margin   (stops early)
  replacement:  may start when   now - last_heartbeat >= TTL + margin (starts late)

Safety depends on the two margins and on clock RATES, never on two servers
agreeing what time it is. The holder stopping is a purely local decision —
no coordination — which is why it is the part that can be trusted.

Two scenarios:
  partition — the holder keeps RUNNING but its renewals and heartbeats no
              longer get through. It writes until its own clock says stop.
  pause     — SIGSTOP. The holder does nothing; CLOCK_MONOTONIC keeps
              counting while it is stopped, so on SIGCONT it already knows.
"""
from __future__ import annotations


def simulate(scenario, ttl=30.0, margin=5.0, fault_at=10.0, fault_for=60.0,
             holder_rate=1.0, step=0.1):
    """holder_rate: how fast the holder's monotonic clock runs relative to the
    watcher's (0.9 = 10% slow — the dangerous direction). Returns
    (replacement time, first overlap time or None, seconds of overlap)."""
    t = 0.0
    last_renewal_h = 0.0          # in the holder's clock
    last_heartbeat_w = 0.0        # in the watcher's clock
    replaced_at = None
    overlap = 0.0
    first = None
    while t < fault_at + fault_for + ttl:
        faulted = fault_at <= t < fault_at + fault_for
        h_now = t * holder_rate
        if not faulted and replaced_at is None:
            last_renewal_h = h_now          # renewals succeed only while unreplaced
            last_heartbeat_w = t
        if replaced_at is None and (t - last_heartbeat_w) >= ttl + margin:
            replaced_at = t
        running = not (scenario == "pause" and faulted)
        writer_allowed = running and (h_now - last_renewal_h) < (ttl - margin)
        if writer_allowed and replaced_at is not None:
            overlap += step
            first = first if first is not None else t
        t += step
    return replaced_at, first, overlap


if __name__ == "__main__":
    rows = [("partition", 30, 5, 1.0), ("partition", 30, 0, 1.0), ("partition", 30, 0, 0.9),
            ("partition", 30, 5, 0.9), ("partition", 30, 5, 0.7), ("pause", 30, 5, 1.0), ("pause", 30, 0, 0.9)]
    print(f"{'scenario':<10} {'ttl':>3} {'margin':>6} {'holder clock':>12}   replacement   overlap")
    for sc, ttl, margin, rate in rows:
        replaced_at, first, overlap = simulate(sc, ttl=ttl, margin=margin, holder_rate=rate)
        ov = "none" if overlap == 0 else f"{overlap:.1f}s from t={first:.1f}s  <-- two writers"
        print(f"{sc:<10} {ttl:>3} {margin:>6} {rate:>11.0%}   at {replaced_at:5.1f}s     {ov}")
