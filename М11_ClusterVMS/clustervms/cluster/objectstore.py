"""The cluster's object store — large, rare, never queried: the restore point.

Two adapters with one contract. `HttpObjectStore` PUTs and GETs against any
endpoint that accepts plain HTTP object semantics (MinIO with a bucket
policy, nginx with dav, an S3 presigned pattern behind a proxy).
`FsObjectStore` is a directory — the tests, and a bench with a shared mount.
An S3 adapter with signed requests is a twenty-line boto3 wrapper on the
same two methods; it is not here because the Node image carries no boto3.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...


class HttpObjectStore:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def put(self, key: str, data: bytes) -> None:
        req = urllib.request.Request(f"{self.base}/{key}", data=data, method="PUT")
        req.add_header("Content-Type", "application/octet-stream")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status not in (200, 201, 204):
                raise IOError(f"PUT {key}: {r.status}")

    def get(self, key: str) -> bytes | None:
        try:
            with urllib.request.urlopen(f"{self.base}/{key}", timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise


class FsObjectStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        p = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)                  # an object appears whole or not at all

    def get(self, key: str) -> bytes | None:
        p = os.path.join(self.root, key)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()


def open_store(url: str) -> ObjectStore:
    if url.startswith(("http://", "https://")):
        return HttpObjectStore(url)
    if url.startswith("file://"):
        return FsObjectStore(url[len("file://"):])
    return FsObjectStore(url)
