"""An object store: large, or frequent, never queried by key. A directory
here; MinIO or S3 in М11. An object appears whole or not at all."""
from __future__ import annotations

import os
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def list(self, prefix: str) -> list[str]: ...


class FsObjectStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _p(self, key: str) -> str:
        if ".." in key or key.startswith("/"):
            raise ValueError(key)
        return os.path.join(self.root, key)

    def put(self, key: str, data: bytes) -> None:
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)

    def get(self, key: str) -> bytes | None:
        try:
            with open(self._p(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def list(self, prefix: str) -> list[str]:
        out = []
        for d, _, files in os.walk(self.root):
            for f in files:
                if f.endswith(".tmp"):
                    continue
                key = os.path.relpath(os.path.join(d, f), self.root)
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)
