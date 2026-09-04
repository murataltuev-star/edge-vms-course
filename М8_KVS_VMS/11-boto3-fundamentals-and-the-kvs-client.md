# Lesson 11 — boto3 Fundamentals and the KVS Client

**Module:** AWS & boto3 (Module 6)
**You will build:** a real (or faithfully fake-tested) `server/kvs.py` — the module that resolves per-API AWS endpoints and hands back cached, ready-to-use boto3 clients — plus the error-handling pattern the rest of Module 6 depends on.
**Time:** ~60–75 minutes.

## Why this lesson exists

Lesson 7 drew a line between two kinds of SDK: a pip-installable one (talks to a network API, ships as pure Python or thin bindings) and a compiled/native one (`kvssink`, which needs a real build). `boto3` — the AWS SDK for Python — is the clearest possible example of the first kind, and this lesson is where that half of the comparison finally gets its own hands-on treatment. Every other lesson in this course has deliberately avoided AWS; this is the first one that doesn't.

Kinesis Video Streams has one wrinkle that trips up almost everyone meeting it for the first time: you cannot call its "give me footage" APIs directly. You have to ask KVS *where* to send that call first, for each API individually, and that answer is only good until the stream's underlying storage shifts to a different endpoint. `server/kvs.py`'s entire job is hiding that wrinkle behind a cache, so the rest of the backend can just say "get me the client for this API" and never think about endpoints again.

## Prerequisites

- Lesson 7 (SDK vs. Docker) — specifically its framing of "pip-installable SDK."
- An AWS account with an IAM user or role that has at least read access to Kinesis Video Streams (`kinesisvideo:*Get*`, `kinesisvideo:ListFragments` at minimum — the full policy comes in a later lesson).
- Credentials configured one of the usual ways: `aws configure` (writes `~/.aws/credentials`), or the `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` / `AWS_DEFAULT_REGION` environment variables.
- `pip install boto3`.

## Learning objectives

1. Explain what boto3 is, and its credential resolution order (why a script that names no credentials anywhere still works).
2. Run a minimal smoke test that proves your credentials work before writing any feature code.
3. Construct a boto3 **client** (not a resource — this project uses clients exclusively) for a named AWS service and region.
4. Catch and interpret AWS errors correctly, via `botocore.exceptions.ClientError` and `e.response["Error"]["Code"]`, instead of a bare `except Exception`.
5. Explain Kinesis Video Streams' control-plane/data-plane split: why `GetDataEndpoint` exists, and why it's per-API rather than per-stream.
6. Build (and verify, with a fake-object test standing in for the real AWS calls) the caching client factory that `server/kvs.py` uses.

---

## Step 1 — Install, and prove your credentials work before writing anything

```bash
mkdir kvs-boto3 && cd kvs-boto3
python3 -m venv .venv
source .venv/bin/activate
pip install boto3
```

Before touching Kinesis Video Streams at all, run the single most useful line of AWS debugging you'll ever write — a smoke test against the *identity* service, which needs no permissions beyond "you are someone":

```python
# whoami.py
import boto3

sts = boto3.client("sts")
identity = sts.get_caller_identity()
print(identity["Account"], identity["Arn"])
```

```bash
python3 whoami.py
```

If this prints an account number and an ARN, your credentials resolve and work — full stop. If it fails, it fails with one of two very different errors, and telling them apart saves real debugging time:

- `NoCredentialsError` — boto3 found *no* credentials anywhere. Nothing is configured.
- `ClientError` with code `InvalidClientTokenId`, `SignatureDoesNotMatch`, or `AccessDenied` — boto3 *found* credentials, but AWS rejected them (or they're valid but lack permission for this specific call). This is a materially different problem from the first one, and you'll be able to tell them apart precisely because Step 4 teaches you to read `ClientError` deliberately instead of just printing the exception.

Get a clean `whoami.py` run before continuing. Everything downstream assumes it.

### Where those credentials actually came from

You didn't pass a key or secret anywhere in `whoami.py` — `boto3.client("sts")` took zero credential arguments. boto3 resolves credentials by checking, in order, roughly:

1. Explicit arguments to `boto3.client(...)` (`aws_access_key_id=...` — never do this for anything but a one-off local experiment; it's the first thing that ends up committed to a repo by accident).
2. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`).
3. The shared credentials file (`~/.aws/credentials`) and config file (`~/.aws/config`), keyed by profile (`AWS_PROFILE` selects which one).
4. An IAM role attached to the compute environment itself (EC2 instance profile, ECS task role, Lambda execution role) — no file, no env var, just "ask the metadata service."

The real project's `server/kvs.py` never specifies a credential source explicitly, for exactly this reason: whatever environment it runs in — your laptop during development, a container in Lesson 8's Docker setup, a real server later — supplies credentials its own way, and the code stays identical. This is the same principle Lesson 8 applied to forwarding credentials into a container by variable name only: the code that *uses* credentials should never be the code that *decides where they come from*.

## Step 2 — A client, not a resource

boto3 offers two API styles: **clients** (thin, one-to-one with the AWS API — every method matches a real HTTP operation name) and **resources** (a higher-level, more Pythonic object model layered on top, available for only some services). Kinesis Video Streams doesn't have a resource interface at all, but even where one exists, this project uses clients exclusively — clients are simpler to reason about (the method name *is* the API call, `ListFragments` → `.list_fragments()`) and every AWS example you'll find online uses them.

```python
import boto3

kinesisvideo = boto3.client("kinesisvideo", region_name="eu-central-1")
```

`"kinesisvideo"` is the **control-plane** client — the one that manages streams as resources (create them, describe them, list them, and — the one method this lesson cares about — hand you an endpoint). It is *not* the client you use to actually fetch footage; that distinction is Step 3's subject.

## Step 3 — The control-plane / data-plane split

Try this, and it will fail:

```python
kinesisvideo.list_fragments(StreamName="cam-01")
```

```
AttributeError: 'KinesisVideo' object has no attribute 'list_fragments'
```

That's not a typo or a missing permission — `list_fragments` genuinely does not exist on the `kinesisvideo` client, because Kinesis Video Streams splits its API surface into two tiers:

- **Control plane** (`kinesisvideo` client): manages streams as named resources — `create_stream`, `describe_stream`, `list_streams`, `tag_stream`, and critically, `get_data_endpoint`. One fixed regional endpoint (e.g. `https://kinesisvideo.eu-central-1.amazonaws.com`) serves every stream in that region.
- **Data plane** (separate clients, one per API group — `kinesis-video-archived-media` for `ListFragments`/`GetHLSStreamingSessionURL`/`GetClip`, `kinesis-video-media` for live `GetMedia`): actually moves or reads footage. Each of these does **not** have one fixed endpoint. Every stream's data is potentially served from a different backend host, and that host can differ **per API**, not just per stream.

That's what `GetDataEndpoint` is for:

```python
resp = kinesisvideo.get_data_endpoint(
    StreamName="cam-01",
    APIName="GET_HLS_STREAMING_SESSION_URL",
)
endpoint = resp["DataEndpoint"]
print(endpoint)   # e.g. https://b-abc123.kinesisvideo.eu-central-1.amazonaws.com
```

Note the `APIName` argument — this is the detail that catches people off guard. You don't call `get_data_endpoint` once per stream; you call it once per **(stream, API)** pair, because `GetHLSStreamingSessionURL` and `ListFragments` can legitimately resolve to different hosts for the same stream. The valid `APIName` values you'll use in this project are `GET_HLS_STREAMING_SESSION_URL` and `LIST_FRAGMENTS`.

Once you have that endpoint, you construct the *actual* client you'll call, pointed at it explicitly:

```python
archived_media = boto3.client(
    "kinesis-video-archived-media",
    endpoint_url=endpoint,
    region_name="eu-central-1",
)
fragments = archived_media.list_fragments(StreamName="cam-01")
```

Two client constructions, two different service names, for what feels like "one API." This is the shape every KVS integration has, and it's exactly what `server/kvs.py` wraps.

## Step 4 — Reading AWS errors deliberately

Ask for a stream that doesn't exist:

```python
try:
    kinesisvideo.get_data_endpoint(
        StreamName="does-not-exist",
        APIName="LIST_FRAGMENTS",
    )
except Exception as e:
    print(type(e), e)
```

You'll get a `botocore.exceptions.ClientError`, and a bare `except Exception` throws away the one thing you actually need: *which* error this was. Every `ClientError` carries a structured `.response` dict — always catch it by name and read the code out explicitly:

```python
from botocore.exceptions import ClientError

try:
    kinesisvideo.get_data_endpoint(
        StreamName="does-not-exist",
        APIName="LIST_FRAGMENTS",
    )
except ClientError as e:
    code = e.response["Error"]["Code"]
    if code == "ResourceNotFoundException":
        print("stream really doesn't exist")
    else:
        raise
```

`e.response["Error"]["Code"]` is a stable string (`ResourceNotFoundException`, `AccessDeniedException`, `InvalidArgumentException`, and so on) — this is the value your code branches on, never `str(e)`, which is a human-readable message that AWS is free to reword at any time without warning. This is precisely the pattern `server/app.py` will use in Lesson 12 to turn `ResourceNotFoundException` into an HTTP 404 — the exact same "translate a specific, named failure into a specific, deliberate status code" discipline Lesson 2 introduced for plain Python data, now applied to a real external failure.

### Verifying the pattern without a live stream

You don't need a real "missing stream" to prove this branch works — you can fake the exact shape `ClientError` has and confirm your handling logic branches correctly, independent of whether boto3 itself is even installed:

```python
class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}

def classify(exc):
    code = exc.response["Error"]["Code"]
    if code == "ResourceNotFoundException":
        return "not_found"
    return "unexpected"

assert classify(FakeClientError("ResourceNotFoundException")) == "not_found"
assert classify(FakeClientError("AccessDeniedException")) == "unexpected"
print("error classification verified")
```

Run it — it passes, and it proves the *branching logic* is correct even before you have real AWS credentials in front of you to trigger the real exception.

## Step 5 — The caching client factory

Calling `get_data_endpoint` on every single request would be wasteful (it's a network round-trip that returns the same answer nearly every time) and calling it exactly once at startup would be wrong (data-plane endpoints can rotate). The real project's answer is a small in-memory cache, keyed by `APIName`, built once and reused: `server/kvs.py`'s `archived_client()`.

```python
# server/kvs.py
import boto3

STREAM_NAME = "cam-01"          # from config, in the real project
AWS_REGION = "eu-central-1"     # from config, in the real project

_kinesisvideo = boto3.client("kinesisvideo", region_name=AWS_REGION)
_archived_clients = {}   # api_name -> boto3 client, built lazily

def archived_client(api_name):
    """Return a cached kinesis-video-archived-media client for this API name,
    resolving and caching its data-plane endpoint on first use."""
    if api_name not in _archived_clients:
        endpoint = _kinesisvideo.get_data_endpoint(
            StreamName=STREAM_NAME,
            APIName=api_name,
        )["DataEndpoint"]
        _archived_clients[api_name] = boto3.client(
            "kinesis-video-archived-media",
            endpoint_url=endpoint,
            region_name=AWS_REGION,
        )
    return _archived_clients[api_name]
```

Callers never touch `_kinesisvideo` or endpoints directly — Lesson 12's `/api/fragments` route will just say `archived_client("LIST_FRAGMENTS").list_fragments(...)`, and `/api/hls` will say `archived_client("GET_HLS_STREAMING_SESSION_URL").get_hls_streaming_session_url(...)`. Each distinct `APIName` gets exactly one `get_data_endpoint` call, ever (for the life of the process), no matter how many requests use it.

### Verifying the caching logic without live AWS calls

This is the part of `kvs.py` with real logic in it — everything else is direct boto3 plumbing — so it's the part worth testing in isolation, the same way Lesson 6 tested `_find_external_camera_pid` with a fake `ps` table instead of the real process list. Stand in fake classes for the two boto3 clients, shaped exactly like the real ones for the one method each that matters here, and count calls:

```python
class FakeKinesisVideoClient:
    def __init__(self):
        self.call_count = 0

    def get_data_endpoint(self, StreamName, APIName):
        self.call_count += 1
        return {"DataEndpoint": f"https://{APIName.lower()}.example.com"}

class FakeArchivedMediaClient:
    def __init__(self, endpoint_url, region_name):
        self.endpoint_url = endpoint_url
        self.region_name = region_name

_kinesisvideo_client = FakeKinesisVideoClient()
_archived_clients = {}
STREAM_NAME = "cam-01"
AWS_REGION = "eu-central-1"

def archived_client(api_name):
    if api_name not in _archived_clients:
        endpoint = _kinesisvideo_client.get_data_endpoint(
            StreamName=STREAM_NAME, APIName=api_name
        )["DataEndpoint"]
        _archived_clients[api_name] = FakeArchivedMediaClient(
            endpoint_url=endpoint, region_name=AWS_REGION
        )
    return _archived_clients[api_name]

# Exercise it: 8 calls across 2 distinct API names.
c1 = archived_client("LIST_FRAGMENTS")
c2 = archived_client("LIST_FRAGMENTS")
c3 = archived_client("GET_HLS_STREAMING_SESSION_URL")
c4 = archived_client("LIST_FRAGMENTS")
c5 = archived_client("GET_HLS_STREAMING_SESSION_URL")
c6 = archived_client("LIST_FRAGMENTS")
c7 = archived_client("GET_HLS_STREAMING_SESSION_URL")
c8 = archived_client("LIST_FRAGMENTS")

assert c1 is c2 is c4 is c6 is c8, "same API name must return the identical cached object"
assert c3 is c5 is c7, "same API name must return the identical cached object"
assert c1 is not c3, "different API names must not share a client"
assert _kinesisvideo_client.call_count == 2, "exactly one real call per distinct API name"

print("caching verified:", _kinesisvideo_client.call_count, "real get_data_endpoint calls for 8 archived_client() calls")
```

Run it:

```bash
python3 kvs_cache_test.py
```

```
caching verified: 2 real get_data_endpoint calls for 8 archived_client() calls
```

This confirms, mechanically rather than by inspection, exactly the property the design is supposed to have: `get_data_endpoint` — the one call in this whole module that costs a network round-trip — happens exactly once per distinct `APIName`, ever. The fakes stand in for boto3 only where boto3 does something this test doesn't care about (making an HTTP call); the caching logic under test is the real, unmodified control flow.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `NoCredentialsError: Unable to locate credentials` | No credential source found at all — check `aws configure list` or that the env vars are actually exported in *this* shell. |
| `ClientError` with code `UnrecognizedClientException` or `InvalidClientTokenId` | Credentials found, but AWS doesn't recognize them — check for a typo, an expired/rotated key, or a leftover `AWS_SESSION_TOKEN` from a different (expired) temporary session. |
| `AttributeError: 'KinesisVideo' object has no attribute 'list_fragments'` | You called a data-plane method on the control-plane client — reread Step 3; you need `boto3.client("kinesis-video-archived-media", endpoint_url=...)`, not `boto3.client("kinesisvideo")`. |
| `get_data_endpoint` succeeds but the later data-plane call times out or connection-refuses | You reused an endpoint from a different `APIName`, or hardcoded a region's generic endpoint instead of the one `get_data_endpoint` actually returned. |
| `ClientError` code `ResourceNotFoundException` on `get_data_endpoint` for a stream you're sure exists | Check region — streams are regional; a stream created in `us-east-1` doesn't exist to a client constructed with `region_name="eu-central-1"`, credentials notwithstanding. |

## Recap

- boto3 resolves credentials through a fixed priority order — explicit arguments, environment variables, shared config files, then the compute environment's own IAM role — so code that never names a source still works everywhere it runs.
- `sts.get_caller_identity()` is a permission-free smoke test: run it first, always, before debugging anything more specific.
- This project uses boto3 **clients**, not resources — one client object per AWS service, methods matching API operation names directly.
- Kinesis Video Streams splits into a control plane (`kinesisvideo`: manages streams, and resolves endpoints) and a data plane (`kinesis-video-archived-media` and others: actually reads/writes footage) — and the data-plane endpoint must be resolved per **(stream, APIName)** pair, not once per stream.
- Catch `botocore.exceptions.ClientError` specifically and branch on `e.response["Error"]["Code"]` — never on `str(e)`.
- `server/kvs.py`'s `archived_client()` caches one data-plane client per `APIName`, verified here to make exactly one `get_data_endpoint` call per distinct name no matter how many times it's requested.

## Exercises

1. Modify `whoami.py` to catch both `NoCredentialsError` (from `botocore.exceptions`) and `ClientError` separately, printing a distinct, specific message for each — this is the same "don't collapse distinct failures into one generic handler" discipline as Step 4.
2. Call `kinesisvideo.get_data_endpoint` for a stream you own with `APIName="LIST_FRAGMENTS"`, then again with `APIName="GET_HLS_STREAMING_SESSION_URL"` — print both endpoints and confirm for yourself whether they're actually the same host or different ones in your account (either answer is "correct"; the point is that the code must never assume which).
3. Add a third fake `APIName` to the caching test's exercise sequence and confirm `_kinesisvideo_client.call_count` becomes 3, not 2 — this is the fastest way to convince yourself the cache key is `api_name` and nothing else.
4. Add a `reset_cache()` function to the caching test's `archived_client` that clears `_archived_clients`, call it after several `archived_client()` calls, then call `archived_client()` again with a previously-used name and confirm `call_count` increases — this is the shape you'd reach for if the project ever needed to handle an expired data-plane endpoint (out of scope for this project, but worth seeing once).

## Where this is going

Lesson 12 puts `archived_client()` to work: `GET /api/fragments` (pagination via `NextToken`, and merging individual fragments into contiguous "runs" using a gap rule) and `GET /api/hls` (validation order, and `GetHLSStreamingSessionURL`'s specific parameters) — the two real AWS-backed routes that replace Lesson 4's fake in-memory data entirely.
