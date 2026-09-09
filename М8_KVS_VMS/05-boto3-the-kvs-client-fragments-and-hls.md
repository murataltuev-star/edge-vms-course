# Lesson 5 — boto3 and the KVS Client — Fragments and HLS, the Real Endpoints

**Module:** KVS-VMS — a cloud VMS on Kinesis Video Streams (Module 8)
**You will build:** a real (or faithfully fake-tested) `server/kvs.py` that resolves per-API endpoints and caches clients; then the two routes that replace the fake recording-status data with real archived footage — fragment pagination, the merge rule, and `/api/hls` with its validation order.
**Time:** ~3–3.5 hours, in two parts.

> **This lesson is in 2 parts** — formerly Lessons 11–12 — and the step numbers run through all of them. Each part ends with its own troubleshooting table, recap and exercises; do the parts in order.

## Prerequisites

**Part A.**
- Lesson 3 (SDK vs. Docker) — specifically its framing of "pip-installable SDK."
- An AWS account with an IAM user or role that has at least read access to Kinesis Video Streams (`kinesisvideo:*Get*`, `kinesisvideo:ListFragments` at minimum — the full policy comes in a later lesson).
- Credentials configured one of the usual ways: `aws configure` (writes `~/.aws/credentials`), or the `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` / `AWS_DEFAULT_REGION` environment variables.
- `pip install boto3`.

**Part B.**
- Part A (`archived_client()`, `ClientError`, the control-plane/data-plane split).
- Lesson 1 (typed query parameters, deliberate status codes) and Lesson 1 (Pydantic `response_model`).
- A KVS stream with at least a few minutes of archived footage, for the optional live-testing steps — everything in this lesson is also verified with fake-object tests that need no such stream.

## Learning objectives

1. Explain what boto3 is, and its credential resolution order (why a script that names no credentials anywhere still works).
2. Run a minimal smoke test that proves your credentials work before writing any feature code.
3. Construct a boto3 **client** (not a resource — this project uses clients exclusively) for a named AWS service and region.
4. Catch and interpret AWS errors correctly, via `botocore.exceptions.ClientError` and `e.response["Error"]["Code"]`, instead of a bare `except Exception`.
5. Explain Kinesis Video Streams' control-plane/data-plane split: why `GetDataEndpoint` exists, and why it's per-API rather than per-stream.
6. Build (and verify, with a fake-object test standing in for the real AWS calls) the caching client factory that `server/kvs.py` uses.
7. Paginate `list_fragments` to exhaustion using `NextToken`, exactly as boto3 documents it.
8. Merge a flat list of fragments into contiguous playback **runs**, using the project's exact gap rule.
9. Convert AWS's `datetime` objects to Unix-epoch floats (and back) at one single boundary, in `models.py`, per Part A's timestamp discipline.
10. Implement `GET /api/hls` with validation performed in the specific order the spec requires — and explain why that order isn't arbitrary.
11. Translate a KVS-specific failure (`ResourceNotFoundException` on an empty on-demand range) into the deliberate HTTP status code a frontend can act on.
12. Verify pagination, merging, and validation order with fake-object tests, independent of whether a real stream is in front of you.

---

## Part A — boto3 Fundamentals and the KVS Client


Lesson 3 drew a line between two kinds of SDK: a pip-installable one (talks to a network API, ships as pure Python or thin bindings) and a compiled/native one (`kvssink`, which needs a real build). `boto3` — the AWS SDK for Python — is the clearest possible example of the first kind, and this lesson is where that half of the comparison finally gets its own hands-on treatment. Every other lesson in this course has deliberately avoided AWS; this is the first one that doesn't.

Kinesis Video Streams has one wrinkle that trips up almost everyone meeting it for the first time: you cannot call its "give me footage" APIs directly. You have to ask KVS *where* to send that call first, for each API individually, and that answer is only good until the stream's underlying storage shifts to a different endpoint. `server/kvs.py`'s entire job is hiding that wrinkle behind a cache, so the rest of the backend can just say "get me the client for this API" and never think about endpoints again.
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

The real project's `server/kvs.py` never specifies a credential source explicitly, for exactly this reason: whatever environment it runs in — your laptop during development, a container in Lesson 3's Docker setup, a real server later — supplies credentials its own way, and the code stays identical. This is the same principle Lesson 3 applied to forwarding credentials into a container by variable name only: the code that *uses* credentials should never be the code that *decides where they come from*.

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

`e.response["Error"]["Code"]` is a stable string (`ResourceNotFoundException`, `AccessDeniedException`, `InvalidArgumentException`, and so on) — this is the value your code branches on, never `str(e)`, which is a human-readable message that AWS is free to reword at any time without warning. This is precisely the pattern `server/app.py` will use in Part B to turn `ResourceNotFoundException` into an HTTP 404 — the exact same "translate a specific, named failure into a specific, deliberate status code" discipline Lesson 1 introduced for plain Python data, now applied to a real external failure.

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

Callers never touch `_kinesisvideo` or endpoints directly — Part B's `/api/fragments` route will just say `archived_client("LIST_FRAGMENTS").list_fragments(...)`, and `/api/hls` will say `archived_client("GET_HLS_STREAMING_SESSION_URL").get_hls_streaming_session_url(...)`. Each distinct `APIName` gets exactly one `get_data_endpoint` call, ever (for the life of the process), no matter how many requests use it.

### Verifying the caching logic without live AWS calls

This is the part of `kvs.py` with real logic in it — everything else is direct boto3 plumbing — so it's the part worth testing in isolation, the same way Lesson 2 tested `_find_external_camera_pid` with a fake `ps` table instead of the real process list. Stand in fake classes for the two boto3 clients, shaped exactly like the real ones for the one method each that matters here, and count calls:

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

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `NoCredentialsError: Unable to locate credentials` | No credential source found at all — check `aws configure list` or that the env vars are actually exported in *this* shell. |
| `ClientError` with code `UnrecognizedClientException` or `InvalidClientTokenId` | Credentials found, but AWS doesn't recognize them — check for a typo, an expired/rotated key, or a leftover `AWS_SESSION_TOKEN` from a different (expired) temporary session. |
| `AttributeError: 'KinesisVideo' object has no attribute 'list_fragments'` | You called a data-plane method on the control-plane client — reread Step 3; you need `boto3.client("kinesis-video-archived-media", endpoint_url=...)`, not `boto3.client("kinesisvideo")`. |
| `get_data_endpoint` succeeds but the later data-plane call times out or connection-refuses | You reused an endpoint from a different `APIName`, or hardcoded a region's generic endpoint instead of the one `get_data_endpoint` actually returned. |
| `ClientError` code `ResourceNotFoundException` on `get_data_endpoint` for a stream you're sure exists | Check region — streams are regional; a stream created in `us-east-1` doesn't exist to a client constructed with `region_name="eu-central-1"`, credentials notwithstanding. |

### Recap

- boto3 resolves credentials through a fixed priority order — explicit arguments, environment variables, shared config files, then the compute environment's own IAM role — so code that never names a source still works everywhere it runs.
- `sts.get_caller_identity()` is a permission-free smoke test: run it first, always, before debugging anything more specific.
- This project uses boto3 **clients**, not resources — one client object per AWS service, methods matching API operation names directly.
- Kinesis Video Streams splits into a control plane (`kinesisvideo`: manages streams, and resolves endpoints) and a data plane (`kinesis-video-archived-media` and others: actually reads/writes footage) — and the data-plane endpoint must be resolved per **(stream, APIName)** pair, not once per stream.
- Catch `botocore.exceptions.ClientError` specifically and branch on `e.response["Error"]["Code"]` — never on `str(e)`.
- `server/kvs.py`'s `archived_client()` caches one data-plane client per `APIName`, verified here to make exactly one `get_data_endpoint` call per distinct name no matter how many times it's requested.

### Exercises

1. Modify `whoami.py` to catch both `NoCredentialsError` (from `botocore.exceptions`) and `ClientError` separately, printing a distinct, specific message for each — this is the same "don't collapse distinct failures into one generic handler" discipline as Step 4.
2. Call `kinesisvideo.get_data_endpoint` for a stream you own with `APIName="LIST_FRAGMENTS"`, then again with `APIName="GET_HLS_STREAMING_SESSION_URL"` — print both endpoints and confirm for yourself whether they're actually the same host or different ones in your account (either answer is "correct"; the point is that the code must never assume which).
3. Add a third fake `APIName` to the caching test's exercise sequence and confirm `_kinesisvideo_client.call_count` becomes 3, not 2 — this is the fastest way to convince yourself the cache key is `api_name` and nothing else.
4. Add a `reset_cache()` function to the caching test's `archived_client` that clears `_archived_clients`, call it after several `archived_client()` calls, then call `archived_client()` again with a previously-used name and confirm `call_count` increases — this is the shape you'd reach for if the project ever needed to handle an expired data-plane endpoint (out of scope for this project, but worth seeing once).

### Where this is going

Part B puts `archived_client()` to work: `GET /api/fragments` (pagination via `NextToken`, and merging individual fragments into contiguous "runs" using a gap rule) and `GET /api/hls` (validation order, and `GetHLSStreamingSessionURL`'s specific parameters) — the two real AWS-backed routes that replace Lesson 1's fake in-memory data entirely.

---

## Part B — `/api/fragments` and `/api/hls`: The Real Endpoints


Part A built `archived_client()` — a way to *get* a working boto3 client for a given API, cached and endpoint-resolved. This lesson is where those clients actually earn their keep: `GET /api/fragments` asks "what footage exists in this time range?" and `GET /api/hls` asks "give me a playable URL for this exact slice of it." Both routes look, in shape, exactly like Lesson 1's typed-query-parameter routes and Lesson 1's deliberate-status-code capstone — the only genuinely new material here is what happens *between* the query parameters and the response: real pagination, a real merging rule, and a validation order that has to be exactly right.
## Step 6 — The timestamp boundary, in `models.py`

Part A stated the rule: *use `ProducerTimestamp` everywhere, and convert between boto3's `datetime` objects and this API's Unix-epoch-seconds floats in exactly one place.* That place is `models.py`:

```python
# server/models.py
from datetime import datetime, timezone
from pydantic import BaseModel

def to_epoch(dt: datetime) -> float:
    """boto3 datetime -> the float this API always sends."""
    return dt.timestamp()

def from_epoch(ts: float) -> datetime:
    """The float this API always receives -> a boto3-compatible datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)

class Run(BaseModel):
    start: float
    end: float

class Window(BaseModel):
    start: float
    end: float

class FragmentsResponse(BaseModel):
    runs: list[Run]
    window: Window

class HLSResponse(BaseModel):
    url: str
```

Every route below calls `from_epoch` exactly once, on the way *in* (turning query parameters into the `datetime` objects boto3 expects), and `to_epoch` exactly once per fragment, on the way *out*. No comparison, no arithmetic, no gap-rule logic anywhere in this lesson touches a `datetime` object directly — once a value crosses this boundary, it's a float, and it stays a float. Verify the conversion round-trips cleanly before building anything on top of it:

```python
original = 1756382400.0
dt = from_epoch(original)
assert dt.tzinfo is not None, "must be timezone-aware — naive datetimes and AWS do not mix"
assert to_epoch(dt) == original
print("timestamp boundary round-trips cleanly")
```

## Step 7 — Pagination to exhaustion

`list_fragments`' response always includes a `NextToken` key — present with a real token when there's more, absent (or `None`) when you've seen everything. The **first** call must include a `FragmentSelector`; every call **after** the first must not repeat it — you resume with `NextToken` alone:

```python
def list_all_fragments(client, stream_name, start_dt, end_dt):
    """Call list_fragments repeatedly until NextToken is exhausted,
    returning every fragment dict boto3 gave back, unmerged and unsorted."""
    fragments = []
    kwargs = {
        "StreamName": stream_name,
        "FragmentSelector": {
            "FragmentSelectorType": "PRODUCER_TIMESTAMP",
            "TimestampRange": {"StartTimestamp": start_dt, "EndTimestamp": end_dt},
        },
    }
    while True:
        resp = client.list_fragments(**kwargs)
        fragments.extend(resp["Fragments"])
        next_token = resp.get("NextToken")
        if not next_token:
            break
        kwargs = {"StreamName": stream_name, "NextToken": next_token}
    return fragments
```

The bug this guards against is subtle and easy to write by accident: reusing the *first* `kwargs` dict on every iteration (or forgetting to drop `FragmentSelector` on subsequent calls) doesn't always fail loudly — some SDKs and services silently ignore the redundant selector. Building a fake client that actually enforces the real rule catches it immediately instead of leaving it as a latent bug that only shows up on a stream with enough fragments to paginate at all:

```python
class FakePaginatingClient:
    """Serves 3 fixed pages, and asserts it's called the way the real API requires."""
    def __init__(self):
        self.pages = [
            {"Fragments": ["f1", "f2"], "NextToken": "tok-a"},
            {"Fragments": ["f3"], "NextToken": "tok-b"},
            {"Fragments": ["f4", "f5"]},  # no NextToken: last page
        ]
        self.call_count = 0

    def list_fragments(self, **kwargs):
        page = self.pages[self.call_count]
        if self.call_count == 0:
            assert "FragmentSelector" in kwargs, "first call must include FragmentSelector"
        else:
            assert "NextToken" in kwargs and "FragmentSelector" not in kwargs, \
                "resumed calls must use NextToken alone"
        self.call_count += 1
        return page

fake = FakePaginatingClient()
result = list_all_fragments(fake, "cam-01", "start-placeholder", "end-placeholder")
assert result == ["f1", "f2", "f3", "f4", "f5"]
assert fake.call_count == 3
print("pagination verified:", fake.call_count, "calls,", len(result), "fragments")
```

## Step 8 — The merging rule

`list_fragments` can return thousands of individual fragments for a busy stream — a few seconds each. Sending that whole list to the browser would mean re-implementing "is this basically one continuous recording" client-side, in JavaScript, for no reason. Instead, the server merges fragments into **runs** before responding, using one precise rule:

> Fragments are contiguous when `next.producer_timestamp - (prev.producer_timestamp + prev.duration) <= 1.0` seconds. Otherwise, start a new run.

```python
def merge_fragments_into_runs(fragments):
    """fragments: list of {"producer_timestamp": float, "duration": float},
    already sorted ascending by producer_timestamp."""
    runs = []
    for frag in fragments:
        start = frag["producer_timestamp"]
        end = start + frag["duration"]
        if runs and (start - runs[-1]["end"]) <= 1.0:
            runs[-1]["end"] = end
        else:
            runs.append({"start": start, "end": end})
    return runs
```

Three things worth noticing in that one function:

- The comparison is against `prev.producer_timestamp + prev.duration` — the previous fragment's *computed end*, not its start. A run's end keeps sliding forward every time a fragment merges into it, which is exactly why `runs[-1]["end"] = end` (not `+= `) is correct: each merge simply replaces the run's end with the newly-merged fragment's end.
- `<= 1.0`, not `< 1.0`. A gap of exactly one second is still one run. Get this backwards and you'll occasionally split a run that should have merged, for reasons that look like nothing was wrong with the actual footage.
- The function assumes its input is already sorted by `producer_timestamp` — which is exactly why `list_all_fragments` in Step 7 doesn't sort, and the real route (Step 9) sorts once, after collecting every page, before merging.

Verify it against a case built to exercise all three branches — a small gap that merges, a large gap that doesn't, and confirm the boundary value itself:

```python
fragments = [
    {"producer_timestamp": 1000.0, "duration": 10.0},   # spans 1000.0-1010.0
    {"producer_timestamp": 1010.4, "duration": 10.0},   # gap 0.4s -> merges
    {"producer_timestamp": 1025.0, "duration": 10.0},   # gap 4.6s -> new run, spans 1025.0-1035.0
    {"producer_timestamp": 1036.0, "duration": 5.0},    # gap exactly 1.0s -> merges
]
runs = merge_fragments_into_runs(fragments)
assert runs == [
    {"start": 1000.0, "end": 1020.4},
    {"start": 1025.0, "end": 1041.0},
]
print("merge rule verified:", len(fragments), "fragments ->", len(runs), "runs")
```

## Step 9 — `GET /api/fragments`

```python
# server/app.py
from fastapi import FastAPI
from server.models import FragmentsResponse, Run, Window, from_epoch, to_epoch
from server.kvs import archived_client

app = FastAPI()

@app.get("/api/fragments", response_model=FragmentsResponse)
def get_fragments(start: float, end: float):
    client = archived_client("LIST_FRAGMENTS")
    raw = list_all_fragments(client, STREAM_NAME, from_epoch(start), from_epoch(end))

    fragments = sorted(
        (
            {
                "producer_timestamp": to_epoch(f["ProducerTimestamp"]),
                "duration": f["FragmentLengthInMilliseconds"] / 1000.0,
            }
            for f in raw
        ),
        key=lambda f: f["producer_timestamp"],
    )
    runs = merge_fragments_into_runs(fragments)

    return FragmentsResponse(
        runs=[Run(start=r["start"], end=r["end"]) for r in runs],
        window=Window(start=start, end=end),
    )
```

Two details worth reading twice:

- `FragmentLengthInMilliseconds` is milliseconds — dividing by `1000.0` at this exact line is the only place that unit conversion happens, right next to the only place the epoch conversion happens, both at the same boundary Step 6 established.
- An empty archive is **not an error**. If `raw` is empty, `runs` ends up `[]`, and the route still returns HTTP 200 with `{"runs": [], "window": {...}}` — there's no special-case branch for "no footage," because the merging function already handles an empty list correctly (the `for` loop simply doesn't execute). This is the same "let the normal code path produce the right empty-case answer, don't add a special case for it" instinct Lesson 1 applied to an empty list of recordings.

## Step 10 — `GET /api/hls`: validation order matters

```python
# server/app.py (continued)
from fastapi import HTTPException
from botocore.exceptions import ClientError
from server.models import HLSResponse

PLAYBACK_CHUNK_SECONDS = 300  # duplicated in web/app.js — see Lesson 4's config note

@app.get("/api/hls", response_model=HLSResponse)
def get_hls(start: float, end: float):
    duration = end - start
    if not (0 < duration <= PLAYBACK_CHUNK_SECONDS):
        raise HTTPException(
            status_code=400,
            detail=f"range must be greater than 0 and at most {PLAYBACK_CHUNK_SECONDS} seconds",
        )

    client = archived_client("GET_HLS_STREAMING_SESSION_URL")
    try:
        resp = client.get_hls_streaming_session_url(
            StreamName=STREAM_NAME,
            PlaybackMode="ON_DEMAND",
            HLSFragmentSelector={
                "FragmentSelectorType": "PRODUCER_TIMESTAMP",
                "TimestampRange": {
                    "StartTimestamp": from_epoch(start),
                    "EndTimestamp": from_epoch(end),
                },
            },
            Expires=300,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(status_code=404, detail="No recording in this range")
        raise

    return HLSResponse(url=resp["HLSStreamingSessionURL"])
```

The validation order is deliberate, and it's checked in this exact sequence for two reasons, one cheap and one conceptual:

1. **Cheap first.** `0 < duration <= PLAYBACK_CHUNK_SECONDS` is pure arithmetic on two numbers you already have — it costs nothing. Calling AWS costs a network round-trip. Reject what you can reject for free before you pay for a request you already know is wrong.
2. **They're different *kinds* of wrong.** A range with `duration <= 0` or `duration > 300` is wrong regardless of what footage exists — the request itself is malformed, independent of the archive's contents, which is exactly HTTP 400's job (Lesson 1: *the client sent something the server should never have to interpret*). A range that's shaped correctly but happens to contain no footage is a **different** kind of absence — the request was well-formed and the resource genuinely isn't there, which is HTTP 404's job. Checking bounds first means a malformed request always gets 400 even on a stream that happens to have no footage anywhere — the two failure reasons never get confused with each other.

`Expires=300` is not an arbitrary round number: it's AWS's documented *minimum* value for this parameter (valid range 300–43200 seconds) — using the floor is a deliberate choice matching the spec's "mint a fresh URL per seek, don't cache or reuse" policy from section 5.3, not an oversight.

The `ResourceNotFoundException`-for-an-empty-range translation deserves one honest caveat: AWS's own public API reference for `get_hls_streaming_session_url` doesn't spell out, in so many words, exactly which exception an empty `ON_DEMAND` range produces — `ResourceNotFoundException` is what the project's own spec states from direct observation, and it's also the same exception code Part A used for "stream doesn't exist," so the `except ClientError` block above is written to check the *code*, not to assume this is the only situation that ever produces it.

### Verifying the order without a live stream

The property worth proving mechanically is the short-circuit itself: an out-of-bounds range must **never** reach AWS at all, and a well-formed range must reach it exactly once, then translate `ResourceNotFoundException` correctly:

```python
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

# Zero-length range: must reject before ever touching the client.
client = FakeHLSClient()
status, body = handle_hls_request(client, start=1000.0, end=1000.0)
assert (status, client.called) == (400, False)

# Too-long range: same — rejected locally, client never called.
client = FakeHLSClient()
status, body = handle_hls_request(client, start=1000.0, end=1500.0)
assert (status, client.called) == (400, False)

# Well-formed range, but no footage there: client IS called, then translated to 404.
client = FakeHLSClient(raise_not_found=True)
status, body = handle_hls_request(client, start=1000.0, end=1010.0)
assert (status, body, client.called) == (404, "No recording in this range", True)

# Well-formed range, footage exists: succeeds normally.
client = FakeHLSClient(raise_not_found=False)
status, body = handle_hls_request(client, start=1000.0, end=1010.0)
assert (status, client.called) == (200, True)

print("validation order verified: bounds checked before any AWS call, 404 translation correct")
```

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `list_fragments` raises `InvalidArgumentException` on the very first call | `TimestampRange` in `FragmentSelector` needs `datetime` objects, not floats — confirm `from_epoch` ran before the call, not after. |
| Pagination loop never terminates | `kwargs` for a resumed call still carries the old `FragmentSelector` alongside `NextToken` — some behavior here is not spec-guaranteed across SDK versions; assert (as the fake client does) that only `NextToken` is present on resumed calls. |
| Two adjacent runs that look continuous in the KVS console show up as separate runs from `/api/fragments` | Check the gap arithmetic uses `prev.producer_timestamp + prev.duration`, not `prev.producer_timestamp` alone — the off-by-one-fragment-duration version of this bug under-counts every gap by one fragment length. |
| `/api/hls` returns 400 for a range you're sure is under 300 seconds | `end - start` can go negative if the two query parameters are swapped by the caller — `0 < duration` catches this, but double-check which one the frontend actually sent as `start` vs `end`. |
| `/api/hls` returns 404 for a range you know has footage | Confirm the route is using `PRODUCER_TIMESTAMP` in `HLSFragmentSelector`, matching `/api/fragments` — mixing selectors (Part A's warning) means the two routes can disagree about what "exists" at the same timestamp. |
| `ClientError` reaches the client uncaught, as a 500 | The `except` block matched on the wrong exception type, or the real error code wasn't `ResourceNotFoundException` — log `e.response["Error"]["Code"]` once, unfiltered, to see what code the real service actually returned before assuming the spec's stated code is wrong. |

### Recap

- `list_fragments` pagination sends `FragmentSelector` on the first call only, and resumes with `NextToken` alone — the fake paginating client's assertions catch the common mistake of getting this backwards.
- The merge rule is precise and its boundary is inclusive: `next.start - prev.end <= 1.0` merges; strictly greater starts a new run. `prev.end` slides forward every merge; it is never the original fragment's own end.
- All `datetime` conversion happens at exactly one boundary (`models.py`'s `to_epoch`/`from_epoch`) — no route, and no helper function below it, ever touches a `datetime` object directly.
- An empty archive is HTTP 200 with `runs: []`, not an error — the merge function's own empty-list behavior already produces the right answer with no special case.
- `/api/hls` validates bounds (400) before ever calling AWS, and only then translates a KVS-specific `ResourceNotFoundException` into a domain-specific 404 — cheap-and-local checks always come before an expensive-and-remote one, and the two failure types are never allowed to collide.
- `Expires=300` is AWS's documented floor for that parameter, not an arbitrary choice — it matches the spec's "mint fresh, don't cache" policy exactly.

### Exercises

1. Add a fifth fragment to Step 8's test data with a gap of exactly `1.000001` seconds after the previous run's end, and confirm it starts a new run — this pins down the boundary from the other side of the one already tested.
2. Extend `FakePaginatingClient` to serve 5 pages instead of 3, and confirm `list_all_fragments` still returns every fragment in order with no code changes — this is the fastest way to convince yourself pagination scales to page count, not just to "more than one."
3. Write a test for `merge_fragments_into_runs([])` (a genuinely empty fragment list) and confirm it returns `[]` without raising — then trace through `get_fragments` by hand and confirm this is exactly what makes the "empty archive → HTTP 200" behavior work with no `if not raw: ...` branch anywhere.
4. In `handle_hls_request`, swap the order of the two checks (call the client first, then validate bounds) and rerun the zero-length-range test — watch it fail on `client.called`, not on the returned status code, and explain in one sentence why that specific assertion is the one that catches an order regression fastest.

## Where this is going

Every piece Module 6 needed — `archived_client()`, real fragment pagination and merging, real validated HLS URLs — now exists and is verified. Lesson 6 is the frontend module, and per how this course has been sequenced, it isn't a new isolated skill: it's where these two routes, Lesson 2's recording controller, and Lesson 4's real `pipeline.py` all get wired into one FastAPI app and one served `web/` folder — an actually-running system, with every step along the way shown working against real state rather than assumed to.
