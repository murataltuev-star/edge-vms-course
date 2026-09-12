"""The one-box console, standard library. Reads never touch a worker;
writes go through the controller, the only writer.

    GET  /cameras                 the read model: every camera from the workers' heartbeats, with age
    GET  /where/<id>              which worker — from the stored placement
    GET  /timeline/<id>?from&to   segments from the archive resource's manifest, fenced ones marked
    POST /cameras                 create (Idempotency-Key required)
    PUT  /cameras/<id>            update — refuses placement and controller-owned fields
    GET  /metrics                 vms_epoch_conflicts, vms_workers_live, vms_cameras_recording
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .archive import ArchiveResource, Manifest
from .controller import Refused, VmsController


def make_handler(ctl: VmsController, archive: ArchiveResource | None):
    seen: dict[str, tuple[int, dict]] = {}

    class H(BaseHTTPRequestHandler):
        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlsplit(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/cameras":
                    return self._send(200, {"rows": ctl.read_model(), "configured": ctl.cameras()})
                if u.path.startswith("/where/"):
                    w = ctl.where(int(u.path.rsplit("/", 1)[1]))
                    return self._send(200 if w else 404, {"worker": w})
                if u.path.startswith("/timeline/") and archive:
                    cid = int(u.path.rsplit("/", 1)[1])
                    return self._send(200, Manifest(archive.root, cid).timeline(float(q.get("from", 0)), float(q.get("to", 1e12))))
                if u.path == "/metrics":
                    hbs = ctl.workers_seen()
                    lines = ["# TYPE vms_epoch_conflicts counter",
                             *[f'vms_epoch_conflicts{{worker="{w}"}} {hb.extra.get("conflicts", 0)}' for w, hb in hbs.items()],
                             "# TYPE vms_workers_live gauge", f"vms_workers_live {len(hbs)}",
                             "# TYPE vms_cameras_recording gauge",
                             f"vms_cameras_recording {sum(1 for hb in hbs.values() for s in hb.status if s['phase'] == 'running')}"]
                    raw = ("\n".join(lines) + "\n").encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", str(len(raw)))
                    self.end_headers(); self.wfile.write(raw); return
                self._send(404, {"detail": "no such route"})
            except Exception as e:                     # noqa: BLE001
                self._send(500, {"detail": str(e)})

        def _idem(self):
            key = self.headers.get("Idempotency-Key")
            if not key:
                self._send(400, {"detail": "Idempotency-Key header is required"}); return None
            if key in seen:
                self._send(*seen[key]); return None
            return key

        def do_POST(self):
            if self.path != "/cameras":
                return self._send(404, {"detail": "no such route"})
            key = self._idem()
            if key is None:
                return
            try:
                r = ctl.create_camera(self._body()); pl = ctl.place(r["id"])
                resp = (201, {**r, "worker": pl.worker if pl else None})
            except Refused as e:
                resp = (400, {"detail": str(e)})
            seen[key] = resp; self._send(*resp)

        def do_PUT(self):
            if not self.path.startswith("/cameras/"):
                return self._send(404, {"detail": "no such route"})
            key = self._idem()
            if key is None:
                return
            try:
                resp = (200, ctl.update_camera(int(self.path.rsplit("/", 1)[1]), self._body()))
            except Refused as e:
                resp = (400, {"detail": str(e)})
            except KeyError:
                resp = (404, {"detail": "no such camera"})
            seen[key] = resp; self._send(*resp)

        def log_message(self, *a):
            pass

    return H


def serve(ctl: VmsController, archive: ArchiveResource | None, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(ctl, archive))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
