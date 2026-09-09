# scripts/create_stream.py — Lesson 6, Step 4. Idempotent: safe to run any
# number of times. ONLY ResourceNotFoundException means "absent, create it";
# every other code (AccessDenied above all) propagates.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ensure_stream(client, name, retention_hours, error_type=None, sleep=time.sleep):
    """Return (arn, created). Safe to call any number of times."""
    if error_type is None:
        from botocore.exceptions import ClientError as error_type
    try:
        info = client.describe_stream(StreamName=name)["StreamInfo"]
        return info["StreamARN"], False
    except error_type as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise                      # AccessDenied is NOT "absent" — let it surface
    client.create_stream(
        StreamName=name,
        DataRetentionInHours=retention_hours,
        MediaType="video/h264",
    )
    for _ in range(30):
        info = client.describe_stream(StreamName=name)["StreamInfo"]
        if info["Status"] == "ACTIVE":
            return info["StreamARN"], True
        sleep(2)
    raise RuntimeError(f"stream {name} never became ACTIVE")


if __name__ == "__main__":
    import boto3
    from server.config import AWS_REGION, RETENTION_HOURS, STREAM_NAME
    client = boto3.client("kinesisvideo", region_name=AWS_REGION)
    arn, created = ensure_stream(client, STREAM_NAME, RETENTION_HOURS)
    print(("created " if created else "already exists ") + arn)
    sys.exit(0)
