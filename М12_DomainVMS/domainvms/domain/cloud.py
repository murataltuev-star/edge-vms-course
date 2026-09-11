"""Lesson 8 — a cluster you rent, and a Node that does not know where it is.

The bandwidth arithmetic, done before the demo: fifty cameras at 4 Mbit/s
is 200 Mbit/s sustained upstream and ~2 TB a day. Most sites cannot buy
that, so recording stays at the edge and operation moves to the cloud —
MIXED is the shape a real deployment takes. And a Node deployed three ways
— local server, rented instance, split — is the SAME artifact; if it is
not, this lesson found a bug in М10 or М11.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass


def bandwidth_mbit(cameras: int, mbit_per_camera: float) -> float:
    return cameras * mbit_per_camera


def tb_per_day(cameras: int, mbit_per_camera: float) -> float:
    return cameras * mbit_per_camera * 1e6 / 8 * 86400 / 1e12


def storage_tb(cameras: int, mbit_per_camera: float, retention_days: int) -> float:
    return tb_per_day(cameras, mbit_per_camera) * retention_days


@dataclass
class Prices:
    hot_object_per_tb_month: float = 22.0     # a public-cloud list price, order of magnitude
    disk_per_tb_once: float = 25.0            # a consumer/enterprise disk, order of magnitude
    disk_life_months: int = 60


def monthly_cost(tb: float, where: str, prices: Prices = Prices()) -> float:
    if where == "cloud":
        return tb * prices.hot_object_per_tb_month
    return tb * prices.disk_per_tb_once / prices.disk_life_months


@dataclass
class Recommendation:
    shape: str            # "edge" | "cloud" | "mixed"
    upstream_mbit: float
    tb_per_day: float
    reason: str


def recommend(cameras: int, mbit_per_camera: float, uplink_mbit: float, retention_days: int = 30,
              prices: Prices = Prices()) -> Recommendation:
    up = bandwidth_mbit(cameras, mbit_per_camera)
    daily = tb_per_day(cameras, mbit_per_camera)
    tb = storage_tb(cameras, mbit_per_camera, retention_days)
    cloud, edge = monthly_cost(tb, "cloud", prices), monthly_cost(tb, "edge", prices)
    if up > uplink_mbit * 0.7:
        return Recommendation("mixed", up, daily,
                              f"{up:.0f} Mbit/s sustained upstream exceeds 70% of a {uplink_mbit:.0f} Mbit/s uplink: "
                              f"recording stays at the edge (~${edge:.0f}/mo in disks), operation moves to the cloud")
    if cameras <= 8:
        return Recommendation("cloud", up, daily,
                              f"{cameras} cameras with no hardware to install: cloud (~${cloud:.0f}/mo) is operationally simpler, "
                              f"not cheaper (edge ~${edge:.0f}/mo); the camera is the buffer on an uplink outage")
    return Recommendation("edge", up, daily,
                          f"the uplink could carry it, but {tb:.1f} TB costs ~${cloud:.0f}/mo hot against ~${edge:.0f}/mo on disks; "
                          f"a datasheet implying otherwise loses money per camera")


def _render_module():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "clustervms", "deploy", "render.py"),
                 os.path.join(os.path.dirname(os.path.dirname(here)), "М11_ClusterVMS", "clustervms", "deploy", "render.py"),
                 os.path.join(os.environ.get("CLUSTERVMS_PATH", ""), "deploy", "render.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("clusterrender", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError("clustervms/deploy/render.py")


def render_three_ways(node: str = "node-3") -> dict[str, str]:
    """The same Node's jobspec for a local rack, a rented instance, and a
    split site. What differs is the datacenter and the object store's
    address. What must never differ: everything about the Node."""
    r = _render_module()
    common = dict(node=node, vlan="cctv-a", cpu=2000, memory=2048, port=8080, lease_ttl=30, lease_margin=5,
                  lost_after="45s", stop_after="25s")
    return {
        "local": r.JOB.format(dc="room-a", object_store="http://minio.room-a:9000/cluster-restore", **common),
        "rented": r.JOB.format(dc="cloud-eu-1", object_store="s3+https://s3.eu-1.example/cluster-restore?region=eu-1", **common),
        "split": r.JOB.format(dc="room-a", object_store="http://minio.room-a:9000/cluster-restore", **common),
    }


def node_stanza(jobspec: str) -> str:
    """The part of a jobspec that is the Node: from `group` down, with the
    two placement-specific lines masked."""
    body = jobspec[jobspec.index("group "):]
    return "\n".join("<placement>" if ("OBJECT_STORE_URL" in ln or "datacenters" in ln) else ln for ln in body.splitlines())
