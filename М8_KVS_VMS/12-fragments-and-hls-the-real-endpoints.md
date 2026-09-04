# Lesson 12 — `/api/fragments` and `/api/hls`: The Real Endpoints

**Module:** AWS & boto3 (Module 6)
**You will build:** the two routes that replace Lesson 4's fake in-memory recording-status data with real archived footage — fragment pagination and merging, and validated, on-demand HLS URLs.
**Time:** ~75–90 minutes.

## Why this lesson exists

Lesson 11 built `archived_client()` — a way to *get* a working boto3 client for a given API, cached and endpoint-resolved. This lesson is where those clients actually earn their keep: `GET /api/fragments` asks "what footage exists in this time range?" and `GET /api/hls` asks "give me a playable URL for this exact slice of it." Both routes look, in shape, exactly like Lesson 2's typed-query-parameter routes and Lesson 4's deliberate-status-code capstone — the only genuinely new material here is what happens *between* the query parameters and the response: real pagination, a real merging rule, and a validation order that has to be exactly right.

## Prerequisites

- Lesson 11 (`archived_client()`, `ClientError`, the control-plane/data-plane split).
- Lesson 2 (typed query parameters, deliberate status codes) and Lesson 3 (Pydantic `response_model`).
- A KVS stream with at least a few minutes of archived footage, for the optional live-testing steps — everything in this lesson is also verified with fake-object tests that need no such stream.

## Learning objectives

1. Paginate `list_fragments` to exhaustion using `NextToken`, exactly as boto3 documents it.
2. Merge a flat list of fragments into contiguous playback **runs**, using the project's exact gap rule.
3. Convert AWS's `datetime` objects to Unix-epoch floats (and back) at one single boundary, in `models.py`, per Lesson 11's timestamp discipline.
4. Implement `GET /api/hls` with validation performed in the specific order the spec requires — and explain why that order isn't arbitrary.
5. Translate a KVS-specific failure (`ResourceNotFoundException` on an empty on-demand range) into the deliberate HTTP status code a frontend can act on.
6. Verify pagination, merging, and validation order with fake-object tests, independent of whether a real stream is in front of you.

---

## Step 1 — The timestamp boundary, in `models.py`

Lesson 11 stated the rule: *use `ProducerTimestamp` everywhere, and convert between boto3's `datetime` objects and this API's Unix-epoch-seconds floats in exactly one place.* That place is `models.py`:

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

## Step 2 — Pagination to exhaustion

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

## Step 3 — The merging rule

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
- The function assumes its input is already sorted by `producer_timestamp` — which is exactly why `list_all_fragments` in Step 2 doesn't sort, and the real route (Step 4) sorts once, after collecting every page, before merging.

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

## Step 4 — `GET /api/fragments`

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

- `FragmentLengthInMilliseconds` is milliseconds — dividing by `1000.0` at this exact line is the only place that unit conversion happens, right next to the only place the epoch conversion happens, both at the same boundary Step 1 established.
- An empty archive is **not an error**. If `raw` is empty, `runs` ends up `[]`, and the route still returns HTTP 200 with `{"runs": [], "window": {...}}` — there's no special-case branch for "no footage," because the merging function already handles an empty list correctly (the `for` loop simply doesn't execute). This is the same "let the normal code path produce the right empty-case answer, don't add a special case for it" instinct Lesson 4 applied to an empty list of recordings.

## Step 5 — `GET /api/hls`: validation order matters

```python
# server/app.py (continued)
from fastapi import HTTPException
from botocore.exceptions import ClientError
from server.models import HLSResponse

PLAYBACK_CHUNK_SECONDS = 300  # duplicated in web/app.js — see Lesson 9's config note

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
2. **They're different *kinds* of wrong.** A range with `duration <= 0` or `duration > 300` is wrong regardless of what footage exists — the request itself is malformed, independent of the archive's contents, which is exactly HTTP 400's job (Lesson 2: *the client sent something the server should never have to interpret*). A range that's shaped correctly but happens to contain no footage is a **different** kind of absence — the request was well-formed and the resource genuinely isn't there, which is HTTP 404's job. Checking bounds first means a malformed request always gets 400 even on a stream that happens to have no footage anywhere — the two failure reasons never get confused with each other.

`Expires=300` is not an arbitrary round number: it's AWS's documented *minimum* value for this parameter (valid range 300–43200 seconds) — using the floor is a deliberate choice matching the spec's "mint a fresh URL per seek, don't cache or reuse" policy from section 5.3, not an oversight.

The `ResourceNotFoundException`-for-an-empty-range translation deserves one honest caveat: AWS's own public API reference for `get_hls_streaming_session_url` doesn't spell out, in so many words, exactly which exception an empty `ON_DEMAND` range produces — `ResourceNotFoundException` is what the project's own spec states from direct observation, and it's also the same exception code Lesson 11 used for "stream doesn't exist," so the `except ClientError` block above is written to check the *code*, not to assume this is the only situation that ever produces it.

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `list_fragments` raises `InvalidArgumentException` on the very first call | `TimestampRange` in `FragmentSelector` needs `datetime` objects, not floats — confirm `from_epoch` ran before the call, not after. |
| Pagination loop never terminates | `kwargs` for a resumed call still carries the old `FragmentSelector` alongside `NextToken` — some behavior here is not spec-guaranteed across SDK versions; assert (as the fake client does) that only `NextToken` is present on resumed calls. |
| Two adjacent runs that look continuous in the KVS console show up as separate runs from `/api/fragments` | Check the gap arithmetic uses `prev.producer_timestamp + prev.duration`, not `prev.producer_timestamp` alone — the off-by-one-fragment-duration version of this bug under-counts every gap by one fragment length. |
| `/api/hls` returns 400 for a range you're sure is under 300 seconds | `end - start` can go negative if the two query parameters are swapped by the caller — `0 < duration` catches this, but double-check which one the frontend actually sent as `start` vs `end`. |
| `/api/hls` returns 404 for a range you know has footage | Confirm the route is using `PRODUCER_TIMESTAMP` in `HLSFragmentSelector`, matching `/api/fragments` — mixing selectors (Lesson 11's warning) means the two routes can disagree about what "exists" at the same timestamp. |
| `ClientError` reaches the client uncaught, as a 500 | The `except` block matched on the wrong exception type, or the real error code wasn't `ResourceNotFoundException` — log `e.response["Error"]["Code"]` once, unfiltered, to see what code the real service actually returned before assuming the spec's stated code is wrong. |

## Recap

- `list_fragments` pagination sends `FragmentSelector` on the first call only, and resumes with `NextToken` alone — the fake paginating client's assertions catch the common mistake of getting this backwards.
- The merge rule is precise and its boundary is inclusive: `next.start - prev.end <= 1.0` merges; strictly greater starts a new run. `prev.end` slides forward every merge; it is never the original fragment's own end.
- All `datetime` conversion happens at exactly one boundary (`models.py`'s `to_epoch`/`from_epoch`) — no route, and no helper function below it, ever touches a `datetime` object directly.
- An empty archive is HTTP 200 with `runs: []`, not an error — the merge function's own empty-list behavior already produces the right answer with no special case.
- `/api/hls` validates bounds (400) before ever calling AWS, and only then translates a KVS-specific `ResourceNotFoundException` into a domain-specific 404 — cheap-and-local checks always come before an expensive-and-remote one, and the two failure types are never allowed to collide.
- `Expires=300` is AWS's documented floor for that parameter, not an arbitrary choice — it matches the spec's "mint fresh, don't cache" policy exactly.

## Exercises

1. Add a fifth fragment to Step 3's test data with a gap of exactly `1.000001` seconds after the previous run's end, and confirm it starts a new run — this pins down the boundary from the other side of the one already tested.
2. Extend `FakePaginatingClient` to serve 5 pages instead of 3, and confirm `list_all_fragments` still returns every fragment in order with no code changes — this is the fastest way to convince yourself pagination scales to page count, not just to "more than one."
3. Write a test for `merge_fragments_into_runs([])` (a genuinely empty fragment list) and confirm it returns `[]` without raising — then trace through `get_fragments` by hand and confirm this is exactly what makes the "empty archive → HTTP 200" behavior work with no `if not raw: ...` branch anywhere.
4. In `handle_hls_request`, swap the order of the two checks (call the client first, then validate bounds) and rerun the zero-length-range test — watch it fail on `client.called`, not on the returned status code, and explain in one sentence why that specific assertion is the one that catches an order regression fastest.

## Where this is going

Every piece Module 6 needed — `archived_client()`, real fragment pagination and merging, real validated HLS URLs — now exists and is verified. Lesson 13 is the frontend module, and per how this course has been sequenced, it isn't a new isolated skill: it's where these two routes, Lesson 6's recording controller, and Lesson 10's real `pipeline.py` all get wired into one FastAPI app and one served `web/` folder — an actually-running system, with every step along the way shown working against real state rather than assumed to.
