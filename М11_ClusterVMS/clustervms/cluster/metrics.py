"""Lesson 28 — the two numbers this module exports, in Prometheus text form,
appended to М10's /metrics.

  node_failover_seconds{kind="last"|"worst"}   the RTO. Worst case is the one that matters.
  node_epoch_conflicts                          zero forever on a healthy cluster. Alarm on it anyway.
  node_config_replicated                        1 if the directory holds the local revision
  node_lease_seconds_left                       how long this instance may still write without a renewal
"""
from __future__ import annotations


def render(host) -> str:
    fo = host.failover or {}
    lines = [
        "# HELP node_failover_seconds Power pulled to recording resumed. Report the worst case.",
        "# TYPE node_failover_seconds gauge",
        f'node_failover_seconds{{node="{host.identity.node}",kind="last"}} {float(fo.get("last", 0)):.1f}',
        f'node_failover_seconds{{node="{host.identity.node}",kind="worst"}} {float(fo.get("worst", 0)):.1f}',
        "# HELP node_epoch_conflicts Times a stale instance of this Node was fenced. Should be zero forever.",
        "# TYPE node_epoch_conflicts counter",
        f'node_epoch_conflicts{{node="{host.identity.node}"}} {host.lease.conflicts if host.lease else 0}',
        "# HELP node_epoch The epoch this instance records into.",
        "# TYPE node_epoch gauge",
        f'node_epoch{{node="{host.identity.node}"}} {host.settings.epoch}',
        "# HELP node_config_replicated 1 if the directory holds this Node's current configuration revision.",
        "# TYPE node_config_replicated gauge",
        f'node_config_replicated{{node="{host.identity.node}"}} {1 if host.replicated_now else 0}',
        "# HELP node_lease_seconds_left Seconds this instance may still write without renewing its lease.",
        "# TYPE node_lease_seconds_left gauge",
        f'node_lease_seconds_left{{node="{host.identity.node}"}} {host.lease.seconds_left() if host.lease else 0:.1f}',
    ]
    return "\n".join(lines) + "\n"
