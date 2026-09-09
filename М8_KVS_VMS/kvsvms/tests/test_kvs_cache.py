"""Lesson 5, Step 5 — one get_data_endpoint per API name, ever."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeKinesisVideoClient:
    def __init__(self): self.call_count = 0
    def get_data_endpoint(self, StreamName, APIName):
        self.call_count += 1
        return {"DataEndpoint": f"https://{APIName.lower()}.example.com"}


def test_endpoint_resolved_once_per_api():
    # Import kvs with a fake boto3 so the module never needs credentials.
    fake_boto3 = types.SimpleNamespace(client=lambda *a, **k: types.SimpleNamespace(**k))
    sys.modules["boto3"] = fake_boto3
    os.environ.setdefault("AWS_REGION", "eu-central-1")
    import importlib
    import server.kvs as kvs
    importlib.reload(kvs)
    ctl = FakeKinesisVideoClient()
    kvs._kinesisvideo = ctl
    kvs._archived_clients.clear()
    for api in ["LIST_FRAGMENTS", "LIST_FRAGMENTS", "GET_HLS_STREAMING_SESSION_URL",
                "LIST_FRAGMENTS", "GET_HLS_STREAMING_SESSION_URL", "LIST_FRAGMENTS",
                "GET_HLS_STREAMING_SESSION_URL", "LIST_FRAGMENTS"]:
        c = kvs.archived_client(api)
        assert c.endpoint_url == f"https://{api.lower()}.example.com"
    assert ctl.call_count == 2
    assert kvs.archived_client("LIST_FRAGMENTS") is kvs.archived_client("LIST_FRAGMENTS")
    del sys.modules["boto3"]
