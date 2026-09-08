"""Lesson 12, Step 5 — validation order: bounds first, AWS never reached."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeHLSClient:
    def __init__(self, raise_not_found=False):
        self.called = False
        self.raise_not_found = raise_not_found

    def get_hls_streaming_session_url(self, **kwargs):
        self.called = True
        if self.raise_not_found:
            raise FakeClientError("ResourceNotFoundException")
        return {"HLSStreamingSessionURL": "https://example.com/session"}


def handle_hls_request(client, start, end, chunk_limit=300):
    """The route's decision, without FastAPI — mirrors server/app.py::get_hls."""
    duration = end - start
    if not (0 < duration <= chunk_limit):
        return 400, f"range must be greater than 0 and at most {chunk_limit} seconds"
    try:
        resp = client.get_hls_streaming_session_url()
    except FakeClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return 404, "No recording in this range"
        raise
    return 200, resp["HLSStreamingSessionURL"]


def test_out_of_bounds_never_reaches_aws():
    c = FakeHLSClient(); assert (handle_hls_request(c, 1000.0, 1000.0)[0], c.called) == (400, False)
    c = FakeHLSClient(); assert (handle_hls_request(c, 1000.0, 1500.0)[0], c.called) == (400, False)


def test_well_formed_reaches_aws_once_and_translates_not_found():
    c = FakeHLSClient(); assert (handle_hls_request(c, 1000.0, 1200.0)[0], c.called) == (200, True)
    c = FakeHLSClient(raise_not_found=True)
    assert handle_hls_request(c, 1000.0, 1200.0) == (404, "No recording in this range")
