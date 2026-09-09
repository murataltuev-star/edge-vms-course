#!/usr/bin/env python3
"""Lesson 29 — the placement service, as a command.

    python3 tools/place.py nodes                      # capacity and labels per Node (from capacity/<node>)
    python3 tools/place.py where 7                    # the directory scan
    python3 tools/place.py add 812 --load 1 --label vlan:cctv-b
    python3 tools/place.py rebalance --budget 5
    python3 tools/place.py capacity node-3 --capacity 50 --labels vlan:cctv-a,vlan:cctv-b

Capacity is (budget − B) / I from shard-memory-probe.py, stored per Node in
capacity/<node>. Placements land in placement/<camera> with a reason and a
revision. Moving a camera (rebalance) is a stop on one Node and a start on
another: this tool RECORDS the decision; the Nodes act on it through their
own configuration, and the epoch keeps the handover safe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cluster.directory import Directory  # noqa: E402
from cluster.placement import Camera, Node, Placer  # noqa: E402
from cluster.variables import NomadVariables  # noqa: E402


def nodes_from(v):
    out = {}
    for path in v.list("capacity/"):
        items, _ = v.get(path)
        n = path.split("/")[1]
        out[n] = Node(n, float(items["capacity"]), frozenset(l for l in items.get("labels", "").split(",") if l))
    return out


def cameras_from(v):
    out = {}
    for path in v.list("placement/"):
        items, _ = v.get(path)
        if items and items.get("node"):
            cid = int(path.split("/")[1])
            out[cid] = Camera(cid, float(items.get("load", "1")), frozenset(l for l in items.get("labels", "").split(",") if l))
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("nodes")
    w = sub.add_parser("where"); w.add_argument("camera", type=int)
    a = sub.add_parser("add"); a.add_argument("camera", type=int); a.add_argument("--load", type=float, default=1.0)
    a.add_argument("--label", action="append", default=[])
    r = sub.add_parser("rebalance"); r.add_argument("--budget", type=int, default=5)
    c = sub.add_parser("capacity"); c.add_argument("node"); c.add_argument("--capacity", type=float, required=True)
    c.add_argument("--labels", default="")
    args = ap.parse_args()
    v = NomadVariables()
    if args.cmd == "capacity":
        _, idx = v.get(f"capacity/{args.node}")
        v.put(f"capacity/{args.node}", {"capacity": args.capacity, "labels": args.labels}, cas=idx)
        print("ok"); return
    if args.cmd == "nodes":
        for n in nodes_from(v).values():
            print(f"{n.id:10} capacity {n.capacity:6.1f}  labels {','.join(sorted(n.labels)) or '-'}")
        return
    if args.cmd == "where":
        print(json.dumps({"camera": args.camera, "node": Directory(v, ttl=0).where(args.camera)})); return
    nodes, cams = nodes_from(v), cameras_from(v)
    p = Placer(nodes, v)
    if args.cmd == "add":
        cam = Camera(args.camera, args.load, frozenset(args.label))
        cams[cam.id] = cam
        pl = p.place(cam, cams)
        if pl is None:
            sys.exit("the system is full")                       # never "Node 3 is full"
        _, idx = v.get(f"placement/{cam.id}")
        items, _ = v.get(f"placement/{cam.id}")
        v.put(f"placement/{cam.id}", dict(items or {}, load=cam.load, labels=",".join(sorted(cam.labels))), cas=idx)
        print(json.dumps({"camera": cam.id, "node": pl.node, "reason": pl.reason, "rev": pl.rev}))
    elif args.cmd == "rebalance":
        for cam_id, frm, to in p.rebalance(cams, args.budget):
            print(json.dumps({"camera": cam_id, "from": frm, "to": to, "reason": p.placed[cam_id].reason}))


if __name__ == "__main__":
    main()
