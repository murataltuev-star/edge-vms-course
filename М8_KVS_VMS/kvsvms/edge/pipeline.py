# edge/pipeline.py — Lesson 4, Step 9: the argv, as a list. Never a shell
# string, never shell=True: gst-launch-1.0 parses its own mini-language and
# nothing here is ever handed to /bin/sh.
from __future__ import annotations


def build_pipeline_argv(clip_path, stream_name, aws_region, retention_hours):
    """М8: the real project's pipeline, publishing straight to kvssink.
    identity sync=true paces filesrc to the clip's own timestamps — without
    it 60 s of video arrive in under a second and the archive gets a sliver."""
    return [
        "gst-launch-1.0", "-q",
        "filesrc", f"location={clip_path}",
        "!", "qtdemux", "name=d", "d.video_0",
        "!", "h264parse",
        "!", "video/x-h264,stream-format=avc,alignment=au",
        "!", "identity", "sync=true",
        "!", "kvssink",
        f"stream-name={stream_name}",
        f"aws-region={aws_region}",
        "storage-size=128",
        f"retention-period={retention_hours}",
    ]


def build_spool_pipeline_argv(clip_path, spool_dir, segment_seconds, launch_epoch):
    """М9 Lesson 4: the one media-layer change. Same source, same parse; the
    sink writes segments to the spool and a separate process uploads them.
    Filenames sort as time: <launch epoch>-<index>.mp4, so oldest-first is a
    sort, and a restart starts a new prefix rather than resuming a file."""
    return [
        "gst-launch-1.0", "-q",
        "filesrc", f"location={clip_path}",
        "!", "qtdemux", "name=d", "d.video_0",
        "!", "h264parse",
        "!", "video/x-h264,stream-format=avc,alignment=au",
        "!", "identity", "sync=true",
        "!", "splitmuxsink",
        f"location={spool_dir}/{int(launch_epoch):010d}-%05d.mp4",
        f"max-size-time={int(segment_seconds) * 1_000_000_000}",
        "muxer-factory=mp4mux",
    ]


def build_upload_argv(path, stream_name, aws_region, retention_hours, file_start_epoch):
    """М9 Lesson 4's uploader: re-publish a CLOSED segment into KVS with its
    ORIGINAL start time. streaming-type=offline makes kvssink wait for the
    service to persist before EOS completes; file-start-time keeps the
    archive's timeline at capture time, not upload time. No identity sync:
    a backlog should drain at line rate, bounded by the spool's budget."""
    return [
        "gst-launch-1.0", "-q",
        "filesrc", f"location={path}",
        "!", "qtdemux", "name=d", "d.video_0",
        "!", "h264parse",
        "!", "video/x-h264,stream-format=avc,alignment=au",
        "!", "kvssink",
        f"stream-name={stream_name}",
        f"aws-region={aws_region}",
        "streaming-type=offline",
        f"file-start-time={int(file_start_epoch)}",
        "storage-size=128",
        f"retention-period={retention_hours}",
    ]
