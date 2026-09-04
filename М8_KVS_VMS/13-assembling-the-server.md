# Lesson 13 — Assembling the Server

**Module:** The Frontend & Assembly (Module 7)
**You will build:** the real project's directory layout, its `.env` configuration, its IAM policy, its two bootstrapping scripts, and — the point of the lesson — one `server/app.py` that serves all five endpoints and the `web/` folder from a single Uvicorn process.
**Time:** ~75–90 minutes.

## Why this lesson exists

Twelve lessons have each produced a working piece in its own folder: a FastAPI app, a process supervisor, a Dockerized edge agent, a GStreamer pipeline, a boto3 client, two AWS-backed routes. None of them have ever run *together*. This lesson is where they stop being twelve exercises and become one program.

That's a real skill, not bookkeeping. Assembly is where you discover that two modules disagree about a config value, that a route you tested alone shadows another one, or that static files silently serve from cache after you edit them. None of those problems are visible while the pieces are apart, and all of them are visible within minutes of putting them together — which is exactly why this lesson ends with you calling every endpoint and reading the actual responses rather than assuming.

## Prerequisites

- Lessons 5, 6 (`looper.py`, `recording.py`), 10 (`pipeline.py`), 11 (`kvs.py`), 12 (`models.py`, both AWS routes). You'll be moving code you already wrote, not writing much new logic.
- An AWS account and credentials that work (`whoami.py` from Lesson 11 passes).
- Python ≥ 3.11.

## Learning objectives

1. Lay out the project the way the spec does, and explain why `edge/` and `server/` have separate `requirements.txt` files.
2. Load one `.env` from both processes, and identify the two constants deliberately duplicated into JavaScript.
3. Write the minimum IAM policy this project needs, and explain each action in it.
4. Write an **idempotent** provisioning script — one that is safe to run any number of times — and prove it is.
5. Write a preflight check that fails with a remediation line instead of a stack trace.
6. Assemble one `app.py` that mounts all five routes plus the static frontend, with correct cache headers, and verify each endpoint by calling it.

---

## Step 1 — The layout

```
kvs-vms-mvp/
├── .env                      # yours, gitignored
├── .env.example              # placeholders, committed
├── Makefile
├── edge/
│   ├── looper.py             # Lessons 5 + 8
│   ├── pipeline.py           # Lesson 10
│   └── requirements.txt      # boto3
├── server/
│   ├── app.py                # this lesson
│   ├── config.py             # this lesson
│   ├── kvs.py                # Lesson 11
│   ├── models.py             # Lesson 12
│   ├── recording.py          # Lesson 6
│   └── requirements.txt      # fastapi, uvicorn, boto3, pydantic, python-dotenv
├── web/
│   ├── index.html            # Lessons 14–15
│   ├── app.js
│   └── style.css
├── docker/kvssink/Dockerfile # Lesson 8
├── media/                    # clip.mp4 (gitignored)
└── scripts/
    ├── create_stream.py      # this lesson
    └── check_env.py          # this lesson
```

**Two `requirements.txt` files, deliberately.** `edge/` and `server/` are conceptually different machines — the edge agent belongs near the camera, the server belongs near the browser. Today they run on your laptop; the split is what keeps that an accident of development rather than an assumption baked into the code. The edge side needs `boto3` only for provisioning, and never needs FastAPI at all.

Create the tree and move your existing files into it now. Everything from Lessons 5–12 goes in unchanged except for import paths.

## Step 2 — One `.env`, loaded once

```bash
# .env.example — commit this; copy to .env and fill in
AWS_REGION=eu-central-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
# AWS_SESSION_TOKEN=...        # only for temporary credentials (SSO / assumed role)
KVS_STREAM_NAME=cam-01
KVS_RETENTION_HOURS=24
CLIP_PATH=./media/clip.mp4
# KVS_DOCKER_IMAGE=kvs-vms-mvp/kvssink   # empty = run kvssink on the host
SERVER_PORT=8000
TIMELINE_WINDOW_MINUTES=60
PLAYBACK_CHUNK_SECONDS=300
```

```python
# server/config.py
import os
from dotenv import load_dotenv

load_dotenv()   # reads .env at the repo root into os.environ, if present

AWS_REGION = os.environ["AWS_REGION"]
STREAM_NAME = os.getenv("KVS_STREAM_NAME", "cam-01")
RETENTION_HOURS = int(os.getenv("KVS_RETENTION_HOURS", "24"))
CLIP_PATH = os.getenv("CLIP_PATH", "./media/clip.mp4")
DOCKER_IMAGE = os.getenv("KVS_DOCKER_IMAGE", "")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
TIMELINE_WINDOW_MINUTES = int(os.getenv("TIMELINE_WINDOW_MINUTES", "60"))
PLAYBACK_CHUNK_SECONDS = int(os.getenv("PLAYBACK_CHUNK_SECONDS", "300"))
```

Note what `config.py` does *not* do: it never reads a credential. `AWS_ACCESS_KEY_ID` and friends sit in `.env` purely so `load_dotenv()` puts them in the environment, where **boto3 finds them by itself** — resolution step 2 from Lesson 11. Your code never touches them, never passes them to a client, and therefore can never accidentally log or serialize them. That's acceptance criterion #9 ("no AWS credential appears in any network response or in page source") satisfied structurally rather than by remembering to be careful.

Two values here are duplicated into JavaScript, because there is no config endpoint and adding one would mean a whole route to serve two integers:

```js
const TIMELINE_WINDOW_MINUTES = 60;
const PLAYBACK_CHUNK_SECONDS = 300;
```

If you change either in `.env`, change it in `web/app.js` too. This is a real (small) design debt, and the honest way to carry it is a comment at both sites saying so — not a pretense that it isn't there.

Add to `.gitignore`:

```
.env
.venv/
media/
edge.log
```

## Step 3 — The IAM policy

Attach this to the IAM user whose credentials are in `.env`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "kinesisvideo:DescribeStream",
      "kinesisvideo:CreateStream",
      "kinesisvideo:GetDataEndpoint",
      "kinesisvideo:PutMedia",
      "kinesisvideo:ListFragments",
      "kinesisvideo:GetHLSStreamingSessionURL"
    ],
    "Resource": "arn:aws:kinesisvideo:*:*:stream/cam-01/*"
  }]
}
```

Every action maps to something you've already built, which is the useful way to read a policy — as a list of the things your program actually does:

| Action | Who needs it | From |
|---|---|---|
| `DescribeStream` | `create_stream.py` (does it exist?) | Step 4 |
| `CreateStream` | `create_stream.py` (make it) | Step 4 |
| `GetDataEndpoint` | `kvs.py`'s `archived_client()` | Lesson 11 |
| `PutMedia` | `kvssink`, publishing frames | Lesson 10 |
| `ListFragments` | `GET /api/fragments` | Lesson 12 |
| `GetHLSStreamingSessionURL` | `GET /api/hls` | Lesson 12 |

The `Resource` line scopes all of it to one stream by name. A policy that reads `"Resource": "*"` would work too — and would also let these credentials delete every other stream in the account. Scope it.

## Step 4 — `create_stream.py`, and what "idempotent" actually costs

Lesson 4 introduced idempotency for an HTTP endpoint: calling `start` twice must not spawn two agents. The same property matters here for a completely different reason — this script runs from `make setup`, which people run repeatedly, often without remembering whether they ran it before.

```python
# scripts/create_stream.py
import sys, time
import boto3
from botocore.exceptions import ClientError
from server.config import AWS_REGION, STREAM_NAME, RETENTION_HOURS

def ensure_stream(client, name, retention_hours):
    """Return (arn, created). Safe to call any number of times."""
    try:
        info = client.describe_stream(StreamName=name)["StreamInfo"]
        return info["StreamARN"], False
    except ClientError as e:
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
        time.sleep(2)
    raise RuntimeError(f"stream {name} never became ACTIVE")

if __name__ == "__main__":
    client = boto3.client("kinesisvideo", region_name=AWS_REGION)
    arn, created = ensure_stream(client, STREAM_NAME, RETENTION_HOURS)
    print(("created " if created else "already exists ") + arn)
    sys.exit(0)
```

The load-bearing line is the `raise` inside the `except`. `ResourceNotFoundException` means "absent, go create it." **Every other** error code — `AccessDeniedException` above all — means something else entirely, and swallowing it would turn a permissions problem into a confusing `CreateStream` failure one line later. This is Lesson 11's "branch on the code, never on the exception type alone" rule doing real work.

Prove the idempotency rather than assuming it, using the same fake-client technique as Lessons 11 and 12:

```python
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

c = FakeKV()
arn, created = ensure_stream(c, "cam-01", 24)      # first run
assert created is True and c.created == [("cam-01", 24, "video/h264")]

arn2, created2 = ensure_stream(c, "cam-01", 24)    # second run
assert created2 is False, "an existing stream must not be re-created"
assert len(c.created) == 1, "idempotent: exactly one CreateStream across two runs"
assert arn2 == arn

class Denied(FakeKV):
    def describe_stream(self, StreamName):
        raise FakeClientError("AccessDeniedException")

try:
    ensure_stream(Denied(), "cam-01", 24)
    raise AssertionError("AccessDenied must not be treated as 'stream absent'")
except FakeClientError as e:
    assert e.response["Error"]["Code"] == "AccessDeniedException"

print("idempotency verified: one CreateStream across two runs; errors propagate")
```

(Run this against a copy of `ensure_stream` with `FakeClientError` swapped in for `ClientError` — or parameterize the exception type. The point is the control flow, which is identical.)

## Step 5 — `check_env.py`: fail with a fix, not a traceback

A preflight script exists to convert "it crashed" into "here is what to do." Four checks, each with a specific remediation line:

```python
# scripts/check_env.py
import os, shutil, subprocess, sys
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from server.config import AWS_REGION, CLIP_PATH, DOCKER_IMAGE

def fail(msg, fix):
    print(f"  FAIL  {msg}\n        → {fix}")
    return False

def ok(msg):
    print(f"  ok    {msg}")
    return True

def check_env_file():
    if not os.path.exists(".env"):
        return fail(".env not found", "cp .env.example .env, then fill in your AWS keys")
    if not os.environ.get("AWS_REGION"):
        return fail("AWS_REGION not set", "add AWS_REGION=<your-region> to .env")
    return ok(f".env loaded (region {AWS_REGION})")

def check_credentials():
    try:
        ident = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()
    except (ClientError, BotoCoreError) as e:
        return fail(f"AWS credentials rejected ({e.__class__.__name__})",
                    "check AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env; "
                    "if using SSO, refresh and set AWS_SESSION_TOKEN")
    return ok(f"credentials valid ({ident['Arn']})")

def check_kvssink():
    if DOCKER_IMAGE:
        found = subprocess.run(["docker", "image", "inspect", DOCKER_IMAGE],
                               capture_output=True).returncode == 0
        return ok(f"docker image {DOCKER_IMAGE} present") if found else fail(
            f"docker image {DOCKER_IMAGE} not found",
            f"docker build -t {DOCKER_IMAGE} docker/kvssink")
    if shutil.which("gst-inspect-1.0") is None:
        return fail("gst-inspect-1.0 not on PATH", "install GStreamer (see README)")
    found = subprocess.run(["gst-inspect-1.0", "kvssink"],
                           capture_output=True).returncode == 0
    return ok("kvssink available on host") if found else fail(
        "no element \"kvssink\"",
        "build the producer SDK and export GST_PLUGIN_PATH, or set KVS_DOCKER_IMAGE in .env")

def check_clip():
    if not os.path.exists(CLIP_PATH):
        return fail(f"{CLIP_PATH} not found",
                    "ffmpeg -f lavfi -i testsrc=duration=60:size=640x480:rate=30 "
                    "-c:v libx264 -an -g 30 -pix_fmt yuv420p " + CLIP_PATH)
    if shutil.which("ffprobe"):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", CLIP_PATH],
            capture_output=True, text=True).stdout.strip()
        return ok(f"{CLIP_PATH} is {out}") if out == "h264" else fail(
            f"{CLIP_PATH} is {out or 'unreadable'}, not h264",
            "re-encode with the ffmpeg command from Lesson 9")
    if DOCKER_IMAGE:
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{os.path.abspath(CLIP_PATH)}:/clip.mp4:ro",
             DOCKER_IMAGE, "gst-launch-1.0", "filesrc", "location=/clip.mp4",
             "!", "qtdemux", "!", "h264parse", "!", "fakesink"],
            capture_output=True).returncode
        return ok("clip demuxes as H.264 (verified in container)") if rc == 0 else fail(
            "clip could not be demuxed as H.264 in the container",
            "re-encode with the ffmpeg command from Lesson 9")
    return ok(f"{CLIP_PATH} exists (not verified — no ffprobe, no docker image)")

if __name__ == "__main__":
    results = [check_env_file(), check_credentials(), check_kvssink(), check_clip()]
    sys.exit(0 if all(results) else 1)
```

`check_clip`'s fallback is worth pausing on. With no `ffprobe` but a Docker image available, it validates the clip by demuxing it *with the same four elements the real pipeline uses* — `filesrc ! qtdemux ! h264parse ! fakesink`, exactly Lesson 9's Step 5 with `fakesink` in place of `filesink`. If those elements can parse it, it is H.264-in-MP4 by construction, because that's precisely the question the real pipeline will ask of it thirty seconds later. Requiring a full local media toolchain purely to validate one file, when the container already contains everything needed, is a bad trade.

## Step 6 — `app.py`: everything in one process

```python
# server/app.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from botocore.exceptions import ClientError

from server import recording                        # Lesson 6
from server.config import STREAM_NAME, PLAYBACK_CHUNK_SECONDS
from server.kvs import archived_client               # Lesson 11
from server.models import (                          # Lesson 12
    FragmentsResponse, HLSResponse, RecordingState, Run, Window,
    from_epoch, to_epoch,
)

app = FastAPI(title="Cloud VMS")

# ---- archive (Lesson 12) --------------------------------------------------

@app.get("/api/fragments", response_model=FragmentsResponse)
def get_fragments(start: float, end: float):
    client = archived_client("LIST_FRAGMENTS")
    raw = list_all_fragments(client, STREAM_NAME, from_epoch(start), from_epoch(end))
    fragments = sorted(
        ({"producer_timestamp": to_epoch(f["ProducerTimestamp"]),
          "duration": f["FragmentLengthInMilliseconds"] / 1000.0} for f in raw),
        key=lambda f: f["producer_timestamp"],
    )
    runs = merge_fragments_into_runs(fragments)
    return FragmentsResponse(
        runs=[Run(**r) for r in runs],
        window=Window(start=start, end=end),
    )

@app.get("/api/hls", response_model=HLSResponse)
def get_hls(start: float, end: float):
    duration = end - start
    if not (0 < duration <= PLAYBACK_CHUNK_SECONDS):
        raise HTTPException(400, f"range must be greater than 0 and at most "
                                 f"{PLAYBACK_CHUNK_SECONDS} seconds")
    try:
        resp = archived_client("GET_HLS_STREAMING_SESSION_URL").get_hls_streaming_session_url(
            StreamName=STREAM_NAME,
            PlaybackMode="ON_DEMAND",
            HLSFragmentSelector={
                "FragmentSelectorType": "PRODUCER_TIMESTAMP",
                "TimestampRange": {"StartTimestamp": from_epoch(start),
                                   "EndTimestamp": from_epoch(end)},
            },
            Expires=300,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(404, "No recording in this range")
        raise
    return HLSResponse(url=resp["HLSStreamingSessionURL"])

# ---- recording control (Lesson 6, now aimed at the real pipeline) ----------

@app.get("/api/recording", response_model=RecordingState)
def recording_status():
    return recording.status()

@app.post("/api/recording/start", response_model=RecordingState)
def recording_start():
    return recording.start()

@app.post("/api/recording/stop", response_model=RecordingState)
def recording_stop():
    try:
        return recording.stop()
    except recording.NotManaged as e:
        raise HTTPException(409, str(e))

# ---- static frontend (must be mounted LAST) -------------------------------

class NoCacheStatic(StaticFiles):
    """Serve web/ with revalidation. There is no build step and no hashed
    filenames, so heuristic browser caching silently serves stale app.js."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

app.mount("/", NoCacheStatic(directory="web", html=True), name="web")
```

Three assembly details that only exist because the pieces are now together:

**Mount order.** `app.mount("/", ...)` claims *every* path under `/`. FastAPI matches routes in declaration order, so the five `/api/...` routes must be declared **before** the mount or the static handler swallows them and every API call returns a 404 from `StaticFiles`. This is the single most common way this assembly goes wrong, and it fails in a way that looks like the routes were never registered at all.

**`Cache-Control: no-cache`.** With no build step there are no content-hashed filenames, so browsers apply *heuristic* caching to `app.js` and `style.css` — meaning they invent an expiry from the last-modified date and then serve a stale copy after you edit the file. `no-cache` doesn't mean "don't cache"; it means "revalidate before using," so the browser still gets a cheap `304 Not Modified` when nothing changed. Without this, "my edit didn't take effect" costs somebody twenty minutes at least once per project.

**`recording.start()` now spawns the real pipeline.** Lesson 6 pointed `recording.py` at `camera_sim.py`. Change that one constant to launch `edge/looper.py`, which since Lesson 10 supervises the real GStreamer pipeline:

```python
# server/recording.py — the only line that changes from Lesson 6
CHILD_SCRIPT = "looper.py"          # was "camera_sim.py"
CHILD_ARGV = [sys.executable, "edge/looper.py"]
```

Everything else in that module — `_reap_if_dead`, the positional `ps` matching, `NotManaged`, the `SIGTERM`→15s→`SIGKILL` escalation — stays exactly as you wrote and tested it in Lesson 6. It supervises a different child now; it does not care which.

## Step 7 — Development fixtures, so the next two lessons have something to draw

There's a scheduling problem in this project that has nothing to do with code: **the timeline needs footage, and footage needs `kvssink`** — the compiled component Lesson 10 deliberately made an optional capstone. Building the frontend against an archive that is legitimately empty means building it blind.

This course has answered that shape of problem the same way five times now: `camera_sim.py` stood in for the pipeline, `videotestsrc` stood in for a camera, `filesink` stood in for `kvssink`, fake boto3 clients stood in for AWS. So do the same thing once more, at the last layer that still needs it:

```python
# server/fixtures.py — development aid. Never enabled in a real run.
import os, time

def enabled():
    return os.getenv("VMS_FIXTURES") == "1"

def runs(window_start, window_end):
    """Four runs with three obvious gaps, positioned relative to 'now'
    so the timeline looks alive whenever you reload."""
    spans = [(-58 * 60, -44 * 60), (-42 * 60, -28.5 * 60),
             (-21 * 60, -9 * 60), (-6 * 60, -20)]
    return [{"start": window_end + a, "end": window_end + b} for a, b in spans]
```

```python
# server/app.py — first two lines of get_fragments
    if fixtures.enabled():
        return FragmentsResponse(runs=[Run(**r) for r in fixtures.runs(start, end)],
                                 window=Window(start=start, end=end))
```

Run the server with `VMS_FIXTURES=1` while building the frontend; run it without for anything real. Two rules make this honest rather than a lie you'll trip over later: the flag is **off by default** (an unset variable can't accidentally ship enabled), and it lives in its own module so there is exactly one place to look when the timeline shows footage you can't explain.

## Step 8 — The Makefile, and the run

```makefile
VENV := .venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r server/requirements.txt -r edge/requirements.txt
	$(PY) scripts/check_env.py
	$(PY) scripts/create_stream.py

stream:
	$(PY) edge/looper.py

serve:
	$(VENV)/bin/uvicorn server.app:app --port $${SERVER_PORT:-8000} --reload

.PHONY: setup stream serve
```

Everything routes through `.venv`. Never invoke a bare `python3` here: on macOS that's a system interpreter which may refuse `pip install` outright.

```bash
make setup     # venv + deps + preflight + stream provisioning
make serve     # leave running
```

## Step 9 — Verify every endpoint, for real

Assembly is not done because the server started. It's done when each endpoint answers correctly. In a second terminal, with `make serve` running:

```bash
# 1. Static frontend is served at the root (a placeholder page is fine for now)
curl -s -o /dev/null -w "index: %{http_code}\n" http://127.0.0.1:8000/

# 2. Static files revalidate rather than caching heuristically
curl -sI http://127.0.0.1:8000/app.js | grep -i cache-control

# 3. Recording status — real process state, from Lesson 6
curl -s http://127.0.0.1:8000/api/recording; echo

# 4. Fragments for the last hour (empty runs is a correct answer)
NOW=$(python3 -c 'import time;print(time.time())')
curl -s "http://127.0.0.1:8000/api/fragments?start=$(python3 -c "print($NOW-3600)")&end=$NOW"; echo

# 5. HLS bounds rejection — must be 400, and must not call AWS
curl -s -w " [%{http_code}]\n" "http://127.0.0.1:8000/api/hls?start=$NOW&end=$(python3 -c "print($NOW+3600)")"

# 6. Recording start/stop round trip
curl -s -X POST http://127.0.0.1:8000/api/recording/start; echo
curl -s http://127.0.0.1:8000/api/recording; echo
curl -s -X POST http://127.0.0.1:8000/api/recording/stop; echo
```

What each result tells you:

| Call | Correct result | What a wrong result means |
|---|---|---|
| 1 | `200` | A 404 means the static mount is missing or `web/` is empty. |
| 2 | `cache-control: no-cache` | Missing header — your `NoCacheStatic` subclass isn't being used. |
| 3 | `{"running": false, "managed": false, "pid": null}` | A 404 here is the mount-order bug from Step 6: static swallowed the API routes. |
| 4 | `{"runs": [...], "window": {...}}`, HTTP 200 | A 500 usually means credentials or region; check `check_env.py` again. An empty `runs` list with 200 is **correct** if nothing has published yet. |
| 5 | `400` naming the 300-second limit | A 500 means the bounds check ran after the AWS call instead of before (Lesson 12, Step 5). |
| 6 | `running` flips true, then false | `start` returning a second pid means idempotency broke in the move; re-read Lesson 6. |

Run all six and read all six before moving on. Every one of them is a real assertion about a system that is now genuinely running, and finding a mount-order bug here costs a minute — finding it while also debugging new JavaScript costs an afternoon.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every `/api/...` call returns 404, but `/` works | The static mount is declared before the API routes. Move `app.mount("/", ...)` to the very bottom of `app.py`. |
| `ModuleNotFoundError: No module named 'server'` | You're running from inside `server/` instead of the repo root — `uvicorn server.app:app` must run from the root, and `scripts/*.py` need the root on `sys.path`. |
| `KeyError: 'AWS_REGION'` at import | `.env` missing or `load_dotenv()` not called before `config.py` reads the environment. |
| Edited `app.js` has no effect in the browser | The `Cache-Control` header isn't being sent — check with `curl -sI`, then hard-reload once to clear what's already cached. |
| `create_stream.py` prints "already exists" for a stream you deleted | Deletion is not instant; wait and re-run. If it persists, you're pointed at a different region than the console tab you deleted it in. |
| `check_env.py` fails on credentials but `whoami.py` from Lesson 11 works | `check_env.py` runs through `.venv` and reads `.env`; your shell may have different credentials exported. That divergence is exactly what the check exists to catch. |
| Uvicorn reloads constantly | `--reload` is watching `media/` or `edge.log`; keep generated files out of watched directories (or drop `--reload`). |

## Recap

- `edge/` and `server/` keep separate `requirements.txt` files because they are conceptually separate machines — the split is what stops "they happen to run on one laptop" becoming an assumption.
- One `.env` feeds both processes; `config.py` reads settings but **never** credentials — boto3 picks those up from the environment on its own, which is what makes leaking them structurally difficult.
- The IAM policy is a one-to-one list of what the program actually does, scoped to one stream by ARN.
- An idempotent provisioning script treats **only** `ResourceNotFoundException` as "absent" and lets every other error code through — verified here with exactly one `CreateStream` across two runs.
- A preflight check's job is to print a remediation line, not a stack trace; `check_clip`'s Docker fallback validates the clip with the same GStreamer elements the real pipeline uses.
- In `app.py`, API routes must be declared before `app.mount("/", ...)`, and static files need `Cache-Control: no-cache` because there is no build step to hash filenames.
- `recording.py` changes by one constant to supervise the real pipeline; everything else about it is unchanged from Lesson 6.
- Fixtures are a labeled, off-by-default development aid — the same "stand-in first" pattern this course has used at every previous layer.

## Exercises

1. Deliberately move `app.mount("/", ...)` above the API route declarations, restart, and run Step 9's check #3. Read the actual 404 body — recognizing it as *the static handler's* 404 rather than FastAPI's is what makes this bug take one minute instead of thirty next time.
2. Run `scripts/create_stream.py` three times in a row and confirm it prints "already exists" twice with no error and no second stream in the AWS console.
3. Temporarily corrupt `AWS_SECRET_ACCESS_KEY` in `.env` and run `check_env.py`. Confirm you get the credentials remediation line and *not* a botocore traceback — then confirm the exit code is 1 (`echo $?`), which is what makes `make setup` stop rather than continue to `create_stream.py`.
4. With the server running, `curl -sI http://127.0.0.1:8000/style.css` twice and compare. Then remove the `Cache-Control` header from `NoCacheStatic`, restart, edit `style.css`, and see how long it takes your browser to notice — this is the failure mode the header exists to prevent, and it's much more convincing witnessed than described.
5. Start the server with `VMS_FIXTURES=1` and hit `/api/fragments`. Confirm you get four runs, then restart without the variable and confirm you get whatever the real archive holds. The point is that the flag's default is the safe one.

## Where this is going

The server is assembled and every endpoint answers. Lesson 14 builds the page that actually consumes it — the timeline, which the spec calls the signature element of the whole interface, and the one place worth spending real design effort.
