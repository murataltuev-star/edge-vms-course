"""Lesson 13, Step 4 — idempotent provisioning; errors other than
ResourceNotFoundException propagate."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.create_stream import ensure_stream


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeKV:
    def __init__(self):
        self.streams = {}
        self.created = []

    def describe_stream(self, StreamName):
        if StreamName not in self.streams:
            raise FakeClientError("ResourceNotFoundException")
        return {"StreamInfo": {"StreamARN": f"arn:...:stream/{StreamName}/1",
                               "Status": self.streams[StreamName]}}

    def create_stream(self, StreamName, DataRetentionInHours, MediaType):
        self.created.append((StreamName, DataRetentionInHours, MediaType))
        self.streams[StreamName] = "ACTIVE"


def test_one_create_across_two_runs():
    c = FakeKV()
    arn, created = ensure_stream(c, "cam-01", 24, error_type=FakeClientError, sleep=lambda s: None)
    assert created is True and c.created == [("cam-01", 24, "video/h264")]
    arn2, created2 = ensure_stream(c, "cam-01", 24, error_type=FakeClientError, sleep=lambda s: None)
    assert created2 is False and len(c.created) == 1 and arn2 == arn


def test_access_denied_is_not_absent():
    class Denied(FakeKV):
        def describe_stream(self, StreamName):
            raise FakeClientError("AccessDeniedException")
    try:
        ensure_stream(Denied(), "cam-01", 24, error_type=FakeClientError)
        raise AssertionError("AccessDenied must not be treated as 'stream absent'")
    except FakeClientError as e:
        assert e.response["Error"]["Code"] == "AccessDeniedException"
