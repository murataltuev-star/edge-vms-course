"""The archive resource as a `system` job: one allocation per server that
has `meta.archive`, pinned there for as long as the server exists.

It has no controller. It has a policy (retention, from each camera's row),
a repair (the manifest made to agree with the files) and two things a
cluster needs that one box did not: a **heartbeat** so the console knows
which resources exist and which are silent, and its **manifests served**
over HTTP so a timeline can be assembled across servers without a shared
filesystem. Footage is served the same way — a range of a file — and it
is the only reader path there is: nothing copies footage between servers.

    vms/resources/<server>/heartbeat   {server, ts, url, usage, cameras: [ids with footage or events here],
                                        units: {subsystem: [unit, ...]} — every subsystem's buckets on this server,
                                        mirrors: {other-server: n} — whose closed buckets this server keeps a copy of}
    GET <url>/manifest/<cam>           the VMS manifest's lines (media and closed event buckets)
    GET <url>/buckets/<sub>/<unit>     any subsystem's closed event buckets, from the files (no manifest needed)
    GET <url>/segment/<path>           the bytes, Range honoured
    GET <url>/events/<path>            one event bucket (also .mirror/<server>/<path>: a copy this server keeps)
    PUT <url>/mirror/<server>/<path>   the knob: another resource leaves a copy of one of ITS closed buckets here
    GET <url>/mirrored/<server>        which of <server>'s buckets this server holds copies of

The events mirror (the storage knob's events row) is a peer copy, not a
store: a resource copies its closed buckets to the next live resource(s)
after it in sorted order, under .mirror/<its name>/ on the peer's disks.
Nobody assigns peers; the rule is the assignment. Lose one server and its
events are searchable from its peer; lose two and one server's events are
gone — the same threshold at which the cluster itself stops. A server that
returns with an empty disk pulls its own buckets back (`restore`).
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.request

from vms.archive import ArchiveResource, Manifest, bucket_from_line
from vms.config import row
from vmsplatform.events import Bucket, buckets_under, parse_bucket, subsystems_under

MIRROR_DIR = ".mirror"


class ResourceHeartbeat:
    def __init__(self, resource: ArchiveResource, objects, server: str, url: str, wall=time.time, prefix: str = "vms"):
        self.res, self.objects, self.server, self.url, self.wall, self.prefix = resource, objects, server, url, wall, prefix

    def cameras_here(self) -> list[int]:
        return self.res.cameras()

    def once(self) -> dict:
        hb = {"server": self.server, "ts": self.wall(), "url": self.url, "usage": self.res.usage(),
              "cameras": self.cameras_here(), "units": subsystems_under(self.res.root),
              "mirrors": {s: len(mirrored_buckets(self.res.root, s)) for s in mirrored_servers(self.res.root)}}
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


MIRROR_KEY = "vms/mirror"            # the knob: {enabled: true|false, copies: 1}; a resource policy, not the controller's


def mirror_settings(vars_) -> dict:
    items, _ = vars_.get(MIRROR_KEY)
    return {"enabled": bool(items) and items.get("enabled") == "true", "copies": int((items or {}).get("copies", 1))}


def mirrored_servers(root: str) -> list[str]:
    try:
        return sorted(d for d in os.listdir(os.path.join(root, MIRROR_DIR)) if os.path.isdir(os.path.join(root, MIRROR_DIR, d)))
    except FileNotFoundError:
        return []


def mirrored_buckets(root: str, server: str, bucket_seconds: int = 600) -> list[Bucket]:
    """Copies this resource holds of <server>'s buckets; `path` is the ORIGINAL path on <server>."""
    base = os.path.join(root, MIRROR_DIR, server)
    out = []
    for d, _, files in os.walk(base):
        for f in files:
            p = os.path.join(d, f)
            parsed = parse_bucket(p, base)
            if parsed:
                sub, unit, epoch, start = parsed
                with open(p) as fh:
                    n = sum(1 for l in fh if l.strip())
                out.append(Bucket(sub, unit, epoch, start, start + bucket_seconds, os.path.relpath(p, base), n))
    return sorted(out, key=lambda b: (b.start, b.epoch))


def peers_of(server: str, live: list[str], copies: int) -> list[str]:
    """The rule that replaces a map: the next `copies` live resources after mine, in sorted order."""
    others = sorted(s for s in live if s != server)
    if not others:
        return []
    after = [s for s in others if s > server] + [s for s in others if s < server]
    return after[:copies]


class PeerClient:
    """How one resource talks to another: HTTP; tests substitute an in-process client."""
    def __init__(self, timeout: float = 5.0): self.timeout = timeout

    def mirrored(self, url: str, server: str) -> list[Bucket]:
        with urllib.request.urlopen(f"{url}/mirrored/{server}", timeout=self.timeout) as r:
            return [bucket_from_line(l) for l in r.read().decode().splitlines() if l.strip()]

    def put(self, url: str, server: str, path: str, data: bytes) -> None:
        req = urllib.request.Request(f"{url}/mirror/{server}/{path}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status not in (200, 201, 204):
                raise IOError(f"PUT mirror {path}: {r.status}")

    def get(self, url: str, server: str, path: str) -> bytes:
        with urllib.request.urlopen(f"{url}/events/{MIRROR_DIR}/{server}/{path}", timeout=self.timeout) as r:
            return r.read()


class ResourcePolicy:
    """The timer job's body: repair, close buckets, retain per camera from the
    config store's row — and, with the knob on, mirror closed buckets to the
    object store. The worker is not involved; the controller is not asked."""

    def __init__(self, resource: ArchiveResource, vars_, wall=time.time, prefix: str = "vms", objects=None, server: str = "",
                 peers: PeerClient | None = None, lost_after: float = 45.0):
        self.res, self.vars, self.wall, self.prefix = resource, vars_, wall, prefix
        self.objects, self.server, self.peers, self.lost_after = objects, server, peers or PeerClient(), lost_after

    def _live_resources(self) -> dict[str, dict]:
        if self.objects is None:
            return {}
        now = self.wall()
        return {s: hb for s, hb in resources_seen(self.objects, self.prefix).items() if now - float(hb["ts"]) <= self.lost_after}

    def closed_buckets(self) -> list[Bucket]:
        out = []
        for sub, units in subsystems_under(self.res.root).items():
            for unit in units:
                out += [b for b in buckets_under(self.res.root, sub, unit, self.res.bucket_seconds) if b.end <= self.wall()]
        return out

    def mirror(self) -> dict:
        """The events row of the storage knob. Every CLOSED bucket on this
        server — any subsystem — is copied to the next live resource(s) after
        it, exactly once each (the peer says what it already holds). This
        server copies only its own buckets, under its own name: one writer
        per key, still. Footage never goes this way."""
        knob = mirror_settings(self.vars)
        if not knob["enabled"] or not self.server:
            return {"enabled": False, "mirrored": 0, "peers": []}
        live = self._live_resources()
        peers = peers_of(self.server, list(live), knob["copies"])
        n = 0
        for peer in peers:
            have = {b.path for b in self.peers.mirrored(live[peer]["url"], self.server)}
            for b in self.closed_buckets():
                if b.path in have:
                    continue
                with open(os.path.join(self.res.root, b.path), "rb") as f:
                    self.peers.put(live[peer]["url"], self.server, b.path, f.read())
                n += 1
        return {"enabled": True, "mirrored": n, "peers": peers}

    def restore(self) -> dict:
        """The reverse, run by the owner: a server back with an empty (or partial)
        resource pulls its own buckets from whoever holds copies, then repairs
        its manifests. Only the owner ever copies back; nothing is rewritten."""
        live = self._live_resources()
        pulled = 0
        for peer, hb in live.items():
            if peer == self.server or self.server not in hb.get("mirrors", {}):
                continue
            for path in sorted(b.path for b in self.peers.mirrored(hb["url"], self.server)):
                dest = os.path.join(self.res.root, path)
                if os.path.exists(dest):
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest + ".tmp", "wb") as f:
                    f.write(self.peers.get(hb["url"], self.server, path))
                os.replace(dest + ".tmp", dest); pulled += 1
        rep = self.res.repair() if pulled else {"added": 0, "dropped": 0}
        return {"pulled": pulled, **rep}

    def once(self, other_subsystems_days: float = 365.0) -> dict:
        rep = self.res.repair()
        closed = len(self.res.close_buckets(self.wall(), bucket_seconds=self.res.bucket_seconds))
        removed = 0
        for cam in self.res.cameras():
            items, _ = self.vars.get(f"{self.prefix}/cameras/{cam}")
            r = row(items) if items else {"retention_days": 30, "events_retention_days": 365}
            removed += self.res.retain(cam, r["retention_days"], self.wall(), r["events_retention_days"])
        # other subsystems' buckets: their own policy is <sub>/retention_days in the store, else a year
        for sub, units in subsystems_under(self.res.root).items():
            if sub == "vms":
                continue
            items, _ = self.vars.get(f"{sub}/retention_days")
            days = float(items["days"]) if items and "days" in items else other_subsystems_days
            for unit in units:
                for b in buckets_under(self.res.root, sub, unit, self.res.bucket_seconds):
                    if b.end < self.wall() - days * 86400:
                        os.remove(os.path.join(self.res.root, b.path)); removed += 1
        return {**rep, "closed": closed, "removed": removed, **self.mirror()}


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
            if self.path.startswith("/mirrored/"):
                server = self.path[len("/mirrored/"):]
                body = "".join(b.line() + "\n" for b in mirrored_buckets(root, server, resource.bucket_seconds)).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body); return
            if self.path.startswith("/buckets/"):
                _, _, sub, unit = self.path.split("/", 3)
                body = "".join(b.line() + "\n" for b in buckets_under(root, sub, unit, resource.bucket_seconds)).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body); return
            if self.path.startswith("/events/"):
                rel = self.path[len("/events/"):]
                p = os.path.join(root, rel)
                if ".." in rel or not rel.endswith(".events.jsonl") or not os.path.isfile(p):   # .mirror/<server>/<path> is fine
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

        def do_PUT(self):
            if not self.path.startswith("/mirror/"):
                self.send_response(404); self.end_headers(); return
            rel = self.path[len("/mirror/"):]
            server, _, path = rel.partition("/")
            if ".." in rel or not server or not path.endswith(".events.jsonl"):
                self.send_response(400); self.end_headers(); return
            dest = os.path.join(root, MIRROR_DIR, server, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            n = int(self.headers.get("Content-Length", 0))
            with open(dest + ".tmp", "wb") as f:
                f.write(self.rfile.read(n))
            os.replace(dest + ".tmp", dest)                                    # a copy appears whole or not at all
            self.send_response(204); self.end_headers()

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
