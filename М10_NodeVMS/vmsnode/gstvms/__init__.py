"""The two GStreamer elements and the actuator that uses them. Needs
PyGObject and GStreamer (python3-gi, gst-plugins-good/bad) — Track 2. The
tests skip this package when `gi` is absent; the logic it calls
(vms.archive) is tested without it.

    driverpacksrc   uri=driverpack://file/<name>: a file from MEDIA_DIR, looping, timestamps rebased
    archivesink     splitmuxsink into the spool; on fragment-closed, promote to the archive resource
"""
