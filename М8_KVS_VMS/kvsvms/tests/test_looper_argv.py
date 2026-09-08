"""Lessons 8 and 10 — argv as a list; credentials by name only; docker wrapping."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edge.pipeline import build_pipeline_argv, build_spool_pipeline_argv


def test_pipeline_argv_shape():
    argv = build_pipeline_argv("./media/clip.mp4", "cam-01", "eu-central-1", 24)
    assert argv[:2] == ["gst-launch-1.0", "-q"]
    assert "identity" in argv and "sync=true" in argv and "kvssink" in argv
    assert all(isinstance(a, str) for a in argv)


def test_spool_argv_writes_sortable_segment_names():
    argv = build_spool_pipeline_argv("./media/clip.mp4", "/data/spool/cam-01", 600, 1757350800.9)
    assert "splitmuxsink" in argv and "kvssink" not in argv
    assert "location=/data/spool/cam-01/1757350800-%05d.mp4" in argv
    assert "max-size-time=600000000000" in argv


def test_docker_argv_forwards_credentials_by_name_only(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3cr3t-value")
    monkeypatch.setenv("KVS_DOCKER_IMAGE", "kvs-vms-mvp/kvssink")
    monkeypatch.setenv("CLIP_PATH", "./media/clip.mp4")
    import importlib
    import server.config
    importlib.reload(server.config)
    import edge.looper as looper
    importlib.reload(looper)
    argv = looper._build_argv()
    assert argv[:3] == ["docker", "run", "--name"]
    assert "AWS_SECRET_ACCESS_KEY" in argv and "s3cr3t-value" not in " ".join(argv)
    assert "gst-launch-1.0" not in argv and "filesrc" in argv     # ENTRYPOINT supplies it
