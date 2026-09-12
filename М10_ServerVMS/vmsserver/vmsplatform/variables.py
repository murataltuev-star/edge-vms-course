"""A config store with the semantics Nomad Variables promise — a
raft-assigned ModifyIndex, PUT with cas=<index> succeeding only if the index
still matches, a conflict otherwise — on one box, as files.

One JSON file per path under <root>/vars/, one counter file for the index,
one lock. Every write is atomic (write-then-rename) and serialised by the
lock, so two processes on the same box see exactly what two clients of one
raft would: one of them wins the CAS.
"""
from __future__ import annotations

import fcntl
import json
import os
from typing import Protocol


class Conflict(Exception):
    """The cas index did not match the current ModifyIndex."""


class Forbidden(Exception):
    """This writer may not write that path — one writer per prefix."""


class Variables(Protocol):
    def get(self, path: str) -> tuple[dict | None, int]: ...
    def put(self, path: str, items: dict, cas: int | None = None) -> int: ...
    def list(self, prefix: str) -> list[str]: ...


def _safe(path: str) -> str:
    if ".." in path or path.startswith("/"):
        raise ValueError(path)
    return path


class FileVariables:
    def __init__(self, root: str, writer: str | None = None, acl: dict[str, list[str]] | None = None):
        self.root = root
        self.dir = os.path.join(root, "vars")
        os.makedirs(self.dir, exist_ok=True)
        self.index_file = os.path.join(root, "index")
        self.lock_file = os.path.join(root, "lock")
        self.writer, self.acl = writer, acl or {}

    def as_writer(self, writer: str, allowed: list[str]) -> "FileVariables":
        """The same store seen through another identity, allowed only these
        prefixes ('vms/*', 'vms/epoch/*') — what a Nomad ACL policy does."""
        v = FileVariables(self.root, writer, dict(self.acl))
        v.acl[writer] = allowed
        return v

    def _file(self, path: str) -> str:
        return os.path.join(self.dir, _safe(path).replace("/", "%2F") + ".json")

    def _locked(self):
        f = open(self.lock_file, "a+")
        fcntl.flock(f, fcntl.LOCK_EX)
        return f

    def _next_index(self) -> int:
        try:
            n = int(open(self.index_file).read() or 1000)
        except FileNotFoundError:
            n = 1000
        n += 1
        tmp = self.index_file + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n))
        os.replace(tmp, self.index_file)
        return n

    def get(self, path: str) -> tuple[dict | None, int]:
        try:
            with open(self._file(path)) as f:
                d = json.load(f)
        except FileNotFoundError:
            return None, 0
        return dict(d["items"]), int(d["index"])

    def put(self, path: str, items: dict, cas: int | None = None) -> int:
        if self.writer is not None and self.acl:
            allowed = self.acl.get(self.writer, [])
            if not any(path == p or (p.endswith("*") and path.startswith(p[:-1])) for p in allowed):
                raise Forbidden(f"{self.writer} may not write {path}")
        with self._locked():
            _, current = self.get(path)
            if cas is not None and cas != current:
                raise Conflict(f"{path}: cas={cas} but ModifyIndex={current}")
            idx = self._next_index()
            tmp = self._file(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"items": {k: str(v) for k, v in items.items()}, "index": idx}, f)
            os.replace(tmp, self._file(path))
            return idx

    def delete(self, path: str, cas: int | None = None) -> None:
        with self._locked():
            _, current = self.get(path)
            if cas is not None and cas != current:
                raise Conflict(f"{path}: cas={cas} but ModifyIndex={current}")
            try:
                os.remove(self._file(path))
            except FileNotFoundError:
                pass
            self._next_index()

    def list(self, prefix: str) -> list[str]:
        out = []
        for f in os.listdir(self.dir):
            if f.endswith(".json"):
                p = f[:-5].replace("%2F", "/")
                if p.startswith(prefix):
                    out.append(p)
        return sorted(out)
