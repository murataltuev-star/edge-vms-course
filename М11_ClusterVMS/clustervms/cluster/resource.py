"""The archive resource as a `system` job: one allocation per server that
has `meta.archive`, pinned there for as long as the server exists.

It has no controller. It has a policy (retention, from each camera's row),
a repair (the manifest made to agree with the files) and two things a
cluster needs that one box did not: a **heartbeat** so the console knows
which resources exist and which are silent, and its **manifests served**
over HTTP so a timeline can be assembled across servers without a shared
filesystem. Footage is served the same way — a range of a file — and it
is the only reader path there is: nothing copies footage between servers.

    vms/resources/<server>/heartbeat   {server, ts, url, usage, cameras: [ids with footage here]}
    GET <url>/manifest/<cam>           the manifest's lines
    GET <url>/segment/<path>           the bytes, Range honoured
    GET <url>/events/<path>            the segment's events file (М10: <start>Z.events.jsonl beside the .mp4)
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vms.archive import ArchiveResource, Manifest
from vms.config import row


class ResourceHeartbeat:
    def __init__(self, resource: ArchiveResource, objects, server: str, url: str, wall=time.time, prefix: str = "vms"):
        self.res, self.objects, self.server, self.url, self.wall, self.prefix = resource, objects, server, url, wall, prefix

    def cameras_here(self) -> list[int]:
        try:
            return sorted(int(d) for d in os.listdir(self.res.root) if d.isdigit())
        except FileNotFoundError:
            return []

    def once(self) -> dict:
        hb = {"server": self.server, "ts": self.wall(), "url": self.url, "usage": self.res.usage(),
              "cameras": self.cameras_here()}
        self.objects.put(f"{self.prefix}/resources/{self.server}/heartbeat", json.dumps(hb).encode())
        return hb


def resources_seen(objects, prefix: str = "vms") -> dict[str, dict]:
    out = {}
    for key in objects.list(f"{prefix}/resources/"):
        if key.endswith("/heartbeat"):
            raw = objects.get(key)
            if raw:
                hb = json.loads(raw)
                out[hb["server"]] = hb
    return out


class ResourcePolicy:
    """The timer job's body: repair, then retain per camera from the config
    store's row. The worker is not involved; the controller is not asked."""

    def __init__(self, resource: ArchiveResource, vars_, wall=time.time, prefix: str = "vms"):
        self.res, self.vars, self.wall, self.prefix = resource, vars_, wall, prefix

    def once(self) -> dict:
        rep = self.res.repair()
        removed = 0
        for cam in sorted(int(d) for d in os.listdir(self.res.root) if d.isdigit()):
            items, _ = self.vars.get(f"{self.prefix}/cameras/{cam}")
            days = row(items)["retention_days"] if items else 30
            removed += self.res.retain(cam, days, self.wall())
        return {**rep, "removed": removed}


def serve(resource: ArchiveResource, host: str = "0.0.0.0", port: int = 8090) -> ThreadingHTTPServer:
    root = resource.root

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            if self.path.startswith("/manifest/"):
                cam = int(self.path.rsplit("/", 1)[1])
                body = "".join(s.line() + "\n" for s in Manifest(root, cam).read()).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body); return
            if self.path.startswith("/events/"):
                rel = self.path[len("/events/"):]
                p = os.path.join(root, rel)
                if ".." in rel or not rel.endswith(".events.jsonl") or not os.path.isfile(p):
                    self.send_response(404); self.end_headers(); return
                with open(p, "rb") as f: data = f.read()
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data); return
            if self.path.startswith("/segment/"):
                rel = self.path[len("/segment/"):]
                p = os.path.join(root, rel)
                if ".." in rel or not os.path.isfile(p):
                    self.send_response(404); self.end_headers(); return
                size = os.path.getsize(p); start, end = 0, size - 1
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    a, b = rng[6:].split("-"); start = int(a or 0); end = int(b) if b else end
                with open(p, "rb") as f:
                    f.seek(start); data = f.read(end - start + 1)
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(data)))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers(); self.wfile.write(data); return
            self.send_response(404); self.end_headers()

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
