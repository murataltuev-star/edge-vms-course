# server/kvs.py — Lesson 11, Step 5: the caching client factory.
# Control plane (kinesisvideo) hands out a data-plane endpoint per
# (stream, API); the data-plane client is built once per API name and reused.
# No credential source is named anywhere: boto3 resolves it from wherever
# this process runs.
import boto3

from server.config import AWS_REGION, STREAM_NAME

_kinesisvideo = None
_archived_clients: dict = {}   # api_name -> boto3 client, built lazily


def control_client():
    global _kinesisvideo
    if _kinesisvideo is None:
        _kinesisvideo = boto3.client("kinesisvideo", region_name=AWS_REGION)
    return _kinesisvideo


def archived_client(api_name: str):
    """Return a cached kinesis-video-archived-media client for this API name,
    resolving and caching its data-plane endpoint on first use."""
    if api_name not in _archived_clients:
        endpoint = control_client().get_data_endpoint(
            StreamName=STREAM_NAME,
            APIName=api_name,
        )["DataEndpoint"]
        _archived_clients[api_name] = boto3.client(
            "kinesis-video-archived-media",
            endpoint_url=endpoint,
            region_name=AWS_REGION,
        )
    return _archived_clients[api_name]
