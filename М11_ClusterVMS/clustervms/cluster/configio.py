"""What travels: the Node's configuration, as one opaque blob.

Lesson 27's table: footage stays, the index is rebuilt, events are
expendable, configuration MUST come. A dump is sites + cameras (operator
columns, the encrypted credential, the revision) + operators + grants.
Nothing controller-owned travels: the new instance re-derives phase and
observed_revision by observation, which is М10 Lesson 21's rule.
"""
from __future__ import annotations

import base64
import json

CAMERA_COLUMNS = ("id", "site_id", "name", "rtsp_url", "cred_username", "cred_secret",
                  "enabled", "retention_days", "priority", "revision")


def encode(sites: list[dict], cameras: list[dict], operators: list[dict], grants: list[dict]) -> bytes:
    cams = []
    for c in cameras:
        row = {k: c.get(k) for k in CAMERA_COLUMNS}
        if row["cred_secret"] is not None:
            row["cred_secret"] = base64.b64encode(bytes(row["cred_secret"])).decode()
        cams.append(row)
    revision = max((c["revision"] for c in cameras), default=0)
    return json.dumps({"format": 1, "revision": revision, "sites": sites, "cameras": cams,
                       "operators": operators, "grants": grants}, sort_keys=True, default=str).encode()


def decode(blob: bytes) -> dict:
    d = json.loads(blob)
    if d.get("format") != 1:
        raise ValueError(f"unknown configuration format {d.get('format')!r}")
    for c in d["cameras"]:
        if c.get("cred_secret") is not None:
            c["cred_secret"] = base64.b64decode(c["cred_secret"])
    return d


def revision_of(blob: bytes) -> int:
    return int(json.loads(blob)["revision"])


class ClusterStoreMixin:
    """The three statements ClusterVMS adds to М10's PgStore. Mixed into
    PgStore for the real thing; tests use FakeClusterStore."""

    async def dump_config(self) -> bytes:
        sites = [dict(r) for r in await self.pool.fetch("SELECT id, name FROM sites ORDER BY id")]
        cams = [dict(r) for r in await self.pool.fetch(
            "SELECT " + ", ".join(CAMERA_COLUMNS) + " FROM cameras ORDER BY id")]
        ops = [dict(r) for r in await self.pool.fetch("SELECT id, username, pwhash FROM operators ORDER BY id")]
        grants = [dict(r) for r in await self.pool.fetch(
            "SELECT id, subject, capability, valid_until FROM grants ORDER BY id")]
        return encode(sites, cams, ops, grants)

    async def config_revision(self) -> int:
        return await self.pool.fetchval("SELECT COALESCE(MAX(revision), 0) FROM cameras")

    async def is_unconfigured(self) -> bool:
        return (await self.pool.fetchval("SELECT count(*) FROM cameras")) == 0

    async def restore_config(self, blob: bytes) -> int:
        """Step 4 of the restore. Rows keep their ids and revisions; the
        sequences are moved past them; the revision trigger is not fired by
        INSERT, so revision comes back exactly as published."""
        d = decode(blob)
        async with self.pool.acquire() as conn, conn.transaction():
            for s in d["sites"]:
                await conn.execute("INSERT INTO sites (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
                                   s["id"], s["name"])
            for c in d["cameras"]:
                await conn.execute(
                    "INSERT INTO cameras (" + ", ".join(CAMERA_COLUMNS) + ") VALUES (" +
                    ", ".join(f"${i+1}" for i in range(len(CAMERA_COLUMNS))) + ") ON CONFLICT (id) DO NOTHING",
                    *[c.get(k) for k in CAMERA_COLUMNS])
            for o in d["operators"]:
                await conn.execute("INSERT INTO operators (id, username, pwhash) VALUES ($1, $2, $3) "
                                   "ON CONFLICT (id) DO NOTHING", o["id"], o["username"], o["pwhash"])
            for g in d["grants"]:
                await conn.execute("INSERT INTO grants (id, subject, capability, valid_until) VALUES ($1, $2, $3, $4) "
                                   "ON CONFLICT (id) DO NOTHING", g["id"], g["subject"], g["capability"], g["valid_until"])
            for table in ("cameras", "operators", "grants"):
                await conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                   f"COALESCE((SELECT MAX(id) FROM {table}), 1))")
        return int(d["revision"])
