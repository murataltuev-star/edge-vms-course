"""driverpack://file/<name> -> a path under MEDIA_DIR. Pure: no GStreamer,
so the worker and the tests can resolve and refuse without a media stack."""
from __future__ import annotations

import os
from urllib.parse import urlsplit


def resolve(uri: str, media_dir: str | None = None) -> str:
    """Anything but driverpack://file/<name> is the real DriverPack's."""
    media_dir = media_dir or os.environ.get("MEDIA_DIR", "/data/media")
    u = urlsplit(uri)
    if u.scheme != "driverpack":
        raise ValueError(f"not a driverpack URI: {uri}")
    if u.netloc != "file":
        raise ValueError(f"driverpack://{u.netloc}/… names a vendor driver; this course ships only driverpack://file/<name>")
    name = u.path.lstrip("/")
    if not name or "/" in name or ".." in name:
        raise ValueError(f"bad media name in {uri}")
    return os.path.join(media_dir, name)
