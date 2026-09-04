# Cloud VMS MVP — Architecture & Build Specification

**Purpose of this document:** a complete, unambiguous brief for generating the project. Everything needed to build it is here; no external context required.

**Status:** reconciled with the implementation as built. Where the build revealed the original brief to be wrong or incomplete — the loop-seam figure, the strict statelessness of the backend, the assumption that AWS ships a prebuilt `kvssink` image — this document has been corrected and says so, rather than leaving the two to disagree. Sections 5.5 and 6.5 are additions; acceptance criterion 10 is currently failing and is marked as such.

**Scope:** one simulated camera (an MP4 file looped in realtime) publishing to Amazon Kinesis Video Streams, plus a single-page web interface that renders a timeline of archived footage and plays back any selected moment.

**Explicit non-goals for this MVP:** multi-camera, authentication, live low-latency view (no WebRTC), motion detection, clip export, deployment, real cameras, multi-tenancy. Do not add them. Do not add abstraction layers anticipating them.

---

## 1. System overview

```
┌──────────────────────────────┐
│  Local machine               │
│                              │
│  clip.mp4                    │
│      │                       │
│      ▼                       │
│  edge/looper.py              │        AWS
│  (supervises GStreamer)      │   ┌──────────────────────┐
│      │                       │   │  Kinesis Video       │
│      │  gst-launch-1.0       │   │  Streams             │
│      │  filesrc ! qtdemux !  │──▶│                      │
│      │  h264parse ! kvssink  │   │  stream: cam-01      │
│      │                       │   │  retention: 24h      │
│                              │   └──────────────────────┘
│  server/app.py (FastAPI)     │            ▲
│      GET /api/fragments      │────────────┘
│      GET /api/hls            │   ListFragments,
│      GET|POST /api/recording │   GetHLSStreamingSessionURL
│      GET /  (static page)    │
│      │                       │
│      ▼                       │
│  browser: timeline + hls.js  │───▶ HLS session URL (direct to AWS)
└──────────────────────────────┘
```

Three processes on one machine. KVS is the only cloud component.

**Why the backend exists at all:** AWS credentials must never reach the browser. The backend is a credential boundary that signs requests on the browser's behalf. It has no database.

It is *almost* stateless. The one exception is the recording supervisor (section 5.5), which holds a handle to the edge agent so the UI can start and stop it. That is a deliberate, contained departure from the original stateless rule — see the note there before extending it.

**Data flow for a seek:** browser sends a timestamp → backend calls `GetHLSStreamingSessionURL` with a bounded time range → browser receives a short-lived signed URL → hls.js fetches media segments **directly from AWS**, not through the backend. The backend never proxies video.

---

## 2. Repository layout

```
kvs-vms-mvp/
├── README.md                 # setup, prerequisites, run order, troubleshooting
├── .env.example
├── Makefile                  # make setup / make stream / make serve
├── edge/
│   ├── looper.py             # supervises the GStreamer pipeline
│   ├── pipeline.py           # builds the gst-launch argv
│   └── requirements.txt      # boto3 (stream provisioning only)
├── server/
│   ├── app.py                # FastAPI app, endpoints + static mount
│   ├── kvs.py                # boto3 client factory, endpoint resolution
│   ├── models.py             # pydantic response models
│   ├── recording.py          # edge-agent supervisor (see 5.5)
│   └── requirements.txt      # fastapi, uvicorn, boto3, pydantic
├── web/
│   ├── index.html
│   ├── app.js                # fetch, timeline render, seek, hls.js wiring
│   └── style.css
├── docker/
│   └── kvssink/Dockerfile    # builds GStreamer + kvssink (see 12)
├── media/                    # clip.mp4 lives here (gitignored)
└── scripts/
    ├── create_stream.py      # idempotent stream provisioning
    └── check_env.py          # preflight: creds, region, kvssink present
```

The Makefile drives everything through a local `.venv`, created by `make setup`. Do not invoke bare `python3`: on macOS that is a system interpreter which may refuse `pip install` outright.

Python ≥ 3.11. Two separate `requirements.txt` because edge and server are conceptually different machines.

A UI-started agent writes its output to `edge.log` at the repo root (gitignored). It is a log, not state — nothing reads it back.

---

## 3. Configuration

Single `.env` at repo root, loaded by both processes.

```
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

`.env.example` ships with placeholder credentials and a comment pointing to the required IAM policy (section 9).

**`KVS_DOCKER_IMAGE`** selects how `kvssink` is run. Empty means the host build; set to an image name, both `edge/looper.py` and `scripts/check_env.py` switch to container mode with no other changes. See section 12.

**`AWS_SESSION_TOKEN`** is only needed for temporary credentials (IAM Identity Center, assumed roles). Prefer a long-lived IAM user for this MVP: temporary credentials expire, and when they do the agent fails mid-run and leaves a growing gap in the archive with no recovery.

**`TIMELINE_WINDOW_MINUTES`** and **`PLAYBACK_CHUNK_SECONDS`** are duplicated as constants at the top of `web/app.js`. There is no config endpoint; if you change one, change both.

---

## 4. Edge component

### 4.1 The problem being solved

KVS expects live-paced media with monotonically increasing wallclock timestamps. A file read from disk gives neither: `filesrc` delivers as fast as the disk allows, and a loop would rewind timestamps. Both break the archive.

Two fixes, both required:

1. **Pacing** — `identity sync=true` throttles the pipeline to the media's own timebase, the GStreamer equivalent of `ffmpeg -re`. Without it, a 60-second clip uploads in ~5 seconds and occupies 5 seconds of archive timeline.
2. **Looping by process restart** — rather than `multifilesrc` or a seek-on-EOS handler, run the pipeline to EOS and relaunch it. Each launch resumes at current wallclock, so timestamps stay monotonic. The seam between runs becomes a genuine recording gap. This is desirable: it gives the timeline something real to render, which is the whole point of the UI.

   Seam length depends on how the pipeline is run: **~200–400ms** on the host, but **~2s** in Docker mode, where each loop starts a fresh container. Measured: 60.0s clip → 61.1s loops, gaps of 2.06–2.20s. Neither is a defect; the larger gap simply renders more clearly.

### 4.2 Pipeline

```
gst-launch-1.0 -q \
  filesrc location=$CLIP_PATH ! \
  qtdemux name=d d.video_0 ! \
  h264parse ! \
  video/x-h264,stream-format=avc,alignment=au ! \
  identity sync=true ! \
  kvssink stream-name=$KVS_STREAM_NAME \
          aws-region=$AWS_REGION \
          storage-size=128 \
          retention-period=$KVS_RETENTION_HOURS
```

No decode, no encode — the H.264 elementary stream is remuxed only. `alignment=au` gives kvssink whole access units. `kvssink` reads credentials from the standard `AWS_*` environment variables.

`pipeline.py` builds this as an argv list from config. Do not use `shell=True`.

When `KVS_DOCKER_IMAGE` is set, `looper.py` wraps that same argv in `docker run` rather than building a different pipeline: the clip's directory is bind-mounted read-only at `/media`, the `location=` token is rewritten to point there, and AWS credentials are forwarded **by name only** (`-e VAR` with no value) so secrets never appear in this process's argv. Docker omits an unset variable entirely, so forwarding `AWS_SESSION_TOKEN` unconditionally is safe.

### 4.3 Supervisor (`looper.py`)

- Preflight: `CLIP_PATH` exists; `gst-inspect-1.0 kvssink` exits 0 — or, in Docker mode, `docker` is on `PATH` and the image exists locally. Fail loudly with a remediation hint if not.
- Loop: `subprocess.run(argv)`, log start/end wallclock and exit code.
- Non-zero exit → exponential backoff, 1s → 30s cap, then keep retrying. A network blip must not kill the agent.
- `SIGINT`/`SIGTERM` → terminate the child, wait, exit 0.
- Log one line per loop: `2026-08-28 14:03:11  loop 47 started`, and on completion the duration.

**Docker mode — stopping the client does not stop the recording.** `docker run` is only a client; the container it starts is a child of the daemon, not of the client. Terminating the client therefore leaves an orphan that keeps publishing to KVS after the supervisor has exited and the UI reports "stopped" — it will happily burn retention and money for as long as you leave it. The container is therefore given a deterministic name (`kvs-vms-edge-<stream>`) and explicitly `docker rm -f`'d both on shutdown and before each loop iteration. The pre-loop removal is not redundant: `docker run --name` fails outright if a container of that name still exists.

### 4.4 Input requirement

The clip must be H.264 in MP4. `README.md` gives the normalization command for anything else:

```
ffmpeg -i input.mkv -c:v libx264 -an -g 30 -pix_fmt yuv420p media/clip.mp4
```

Keep GOP short (`-g 30`). Long GOPs make fragments large and coarsen seek granularity. Drop audio (`-an`) — it adds nothing here and complicates the pipeline.

---

## 5. Backend

FastAPI, two JSON endpoints, plus static file mount at `/`. Stateless. No database.

### 5.1 KVS client construction (`kvs.py`)

Archived-media calls require a per-API data endpoint obtained via `GetDataEndpoint`. This resolution is a network call, so cache it per API name for the process lifetime.

```python
def archived_client(api_name: str):
    # api_name ∈ {"LIST_FRAGMENTS", "GET_HLS_STREAMING_SESSION_URL"}
    # 1. kinesisvideo.get_data_endpoint(StreamName=..., APIName=api_name)
    # 2. boto3.client("kinesis-video-archived-media", endpoint_url=<that>)
    # cache by api_name
```

### 5.2 `GET /api/fragments`

**Query:** `start: float`, `end: float` — Unix epoch seconds.

**Behavior:** call `list_fragments` with `FragmentSelectorType="PRODUCER_TIMESTAMP"`, paginate `NextToken` to exhaustion, sort by timestamp ascending.

**Response:**

```json
{
  "runs": [
    {"start": 1756382400.0, "end": 1756382460.0},
    {"start": 1756382460.4, "end": 1756382520.4}
  ],
  "window": {"start": 1756378800.0, "end": 1756382400.0}
}
```

**Merging rule:** fragments are contiguous when `next.producer_timestamp - (prev.producer_timestamp + prev.duration) <= 1.0` seconds. Otherwise start a new run. Merge server-side; the browser receives runs, not thousands of fragments.

Empty archive → `{"runs": [], "window": {...}}` with HTTP 200. Not an error.

### 5.3 `GET /api/hls`

**Query:** `start: float`, `end: float`.

**Behavior:** call `get_hls_streaming_session_url` with `PlaybackMode="ON_DEMAND"`, `Expires=300`, `HLSFragmentSelector` using `PRODUCER_TIMESTAMP` and the given range.

**Response:** `{"url": "https://..."}`

**Validation, in this order:**
- `end - start` must be `0 < d <= PLAYBACK_CHUNK_SECONDS`. Otherwise 400 with a message naming the limit.
- Range containing no fragments → 404 `{"detail": "No recording in this range"}`. KVS returns an opaque error for empty ON_DEMAND ranges; catch `ResourceNotFoundException` and translate it.

Mint a fresh URL per seek. Do not cache or reuse — they expire, and one-per-seek is simpler than session tracking.

### 5.4 Timestamp discipline

**Use `ProducerTimestamp` everywhere.** Both endpoints, both selectors. `ServerTimestamp` differs by ingest latency; mixing the two makes the timeline and the player disagree by seconds, and the resulting bug looks like a player problem for about a day before anyone suspects the selector.

All timestamps crossing the API boundary are **Unix epoch seconds as floats**. boto3 returns and expects `datetime` objects — convert at the boundary in `models.py`, nowhere else.

### 5.5 Recording control (`recording.py`)

Supervises the edge agent so the UI can start and stop it (section 6.5), making `make stream` optional.

**This is the one place the backend holds state** — a `Popen` handle to the agent. It is confined to `server/recording.py` so the two KVS endpoints above stay pure request/response. Keep it that way; if this module starts growing a job queue or persisting anything, the design has gone wrong.

**Endpoints:**
- `GET /api/recording` → `{"running": bool, "managed": bool, "pid": int|null}`
- `POST /api/recording/start` → same shape. Idempotent: if an agent is already running it returns the current state rather than spawning a second one. Two publishers on one stream would interleave fragments.
- `POST /api/recording/stop` → same shape, or **409** if `managed` is false.

**`managed` distinguishes ownership.** True means this server spawned the agent. False means it was started externally (`make stream`) — such an agent is *reported* but not killed; the user is watching it in their own terminal and it is not the server's to terminate.

**Detecting an external agent:** scan `ps` output for a process whose argv[0] is a Python interpreter with `looper.py` among its arguments. Do **not** use `pgrep -f looper.py`: `make stream` runs the agent via `sh -c`, so the shell wrapper's command line also contains that path and will match first, yielding the wrong pid — and a stale match makes `start` refuse forever.

**Stopping:** SIGTERM, wait 15s, escalate to SIGKILL only if ignored. The agent's own signal handler is what removes the container (section 4.3).

`status()` reaps a dead handle: if `Popen.poll()` shows the child exited, clear it and fall through to the external scan, so a crashed agent reports `running: false` rather than a phantom pid.

---

## 6. Frontend

One page, no framework, no build step. `hls.js` from CDN. Vanilla JS modules.

### 6.1 Layout

```
┌──────────────────────────────────────────────┐
│  cam-01            ● recording   [ Stop ]    │   status strip
├──────────────────────────────────────────────┤
│                                              │
│              <video> player                  │
│                                              │
├──────────────────────────────────────────────┤
│ 13:00      13:15      13:30      13:45  14:00│   tick labels
│ ▓▓▓▓▓▓▓▓░▓▓▓▓▓▓▓▓▓▓▓▓░▓▓▓▓▓▓▓▓▓▓▓░▓▓▓▓▓▓▓▓▓▓ │   timeline
│                    ▲                          │   playhead
└──────────────────────────────────────────────┘
```

### 6.2 Timeline behavior

- On load, fetch `/api/fragments` for the last `TIMELINE_WINDOW_MINUTES`.
- Render each run as an absolutely-positioned bar; `left` and `width` are percentages of window duration. Gaps are the background showing through — do not render gap elements.
- Poll every 10s and re-render. The window slides; it always ends at "now."
- Click on a bar → convert click X to a timestamp → request `PLAYBACK_CHUNK_SECONDS` starting there, clamped to the run's end → `GET /api/hls` → `hls.loadSource(url)` → play.
- Click on a gap → no request. Cursor is `default` over gaps, `pointer` over runs. The interface should make un-clickable regions obviously un-clickable rather than punishing the click with an error.
- Playhead tracks `video.currentTime` offset from the loaded chunk's start.

### 6.3 States

- **Empty archive:** "No recording yet. Press Start to begin." Not an error state — an instruction.
- **Loading a chunk:** timeline stays interactive, player shows a quiet indicator.
- **HLS error:** "Playback failed for this segment" plus a retry affordance. Do not surface AWS error codes to the viewer; log them to console.

Any element whose visibility is driven by the `hidden` attribute needs an explicit `[hidden] { display: none }` rule if it also carries an author `display`. Author styles beat the UA stylesheet's `[hidden]` rule, so a panel styled `display: flex` never hides no matter what the JS sets.

### 6.4 Visual direction

This is a surveillance console, not a dashboard. Design it accordingly — the reference points are broadcast monitoring and editing timelines, not admin panels.

Deliberate choices, not defaults:

- **Palette:** dark, but not black-with-a-neon-accent. Build on a desaturated cool grey ground (`#1A1D21`) with recording bars in a slightly warm off-white (`#E8E4DC`) so footage-present reads as *presence* rather than as an accent color. One signal color for the playhead only. Gaps are the ground.
- **Type:** a monospaced face for all timecodes and clock labels — timestamps are data and should align in columns. A distinct grotesque for the small amount of prose. Do not set timecodes in the body face.
- **Signature element:** the timeline itself. Give it real density — hairline minute ticks, heavier five-minute ticks, labeled quarter-hours — so the gaps read as physically measured absences rather than styled dividers. This is the one place to spend effort; everything else stays quiet.
- **Motion:** the playhead moves and nothing else. No transitions on the bars, no hover animations. Stillness makes the moving element mean something.

Quality floor: keyboard-focusable timeline runs, visible focus ring, `prefers-reduced-motion` respected, usable down to 380px wide.

### 6.5 Recording control

A Start/Stop button sits in the status strip beside the status text.

- Status text reflects the **agent**, not the archive: `● recording` / `○ stopped`. Deriving it from "are there fragments?" is wrong — old footage exists long after recording stops.
- Every displayed state comes from a server response: the POST's return value, or the same 10s poll that refreshes the timeline. The button never trusts its own optimistic guess, so an agent that dies on its own — or is stopped in another terminal — corrects itself within one poll cycle.
- While a request is in flight the button disables and reads `Starting…` / `Stopping…`.
- A `managed: false` agent shows a tooltip explaining it must be stopped where it was started; pressing Stop surfaces the 409's message rather than a raw status code.
- The button is styled quietly, **not** in the signal color — 6.4 reserves that for the playhead. Recording state reads through the same off-white used for footage-present.

---

## 7. Bootstrapping

`scripts/create_stream.py` — idempotent. `DescribeStream`; if `ResourceNotFoundException`, `CreateStream` with `DataRetentionInHours` and `MediaType="video/h264"`, then poll until `ACTIVE`. Print the ARN. Safe to run repeatedly.

`scripts/check_env.py` — preflight, run by `make setup`:
1. `.env` present and parseable
2. `sts:GetCallerIdentity` succeeds
3. `gst-inspect-1.0 kvssink` exits 0 — or, in Docker mode, the image exists locally
4. `CLIP_PATH` exists and is H.264

Each failure prints a specific remediation line, not a stack trace.

Check 4 uses `ffprobe` when present. With no host `ffprobe` but `KVS_DOCKER_IMAGE` set, it demuxes the clip inside the container with the same elements the real pipeline uses (`filesrc ! qtdemux ! h264parse ! fakesink`) — if that succeeds the file is H.264 in MP4 by construction. Requiring a local ffmpeg install purely to validate a file is a bad trade when the container already has everything needed.

---

## 8. Run order

```
make setup     # venv + pip install + check_env + create_stream
make stream    # edge/looper.py — leave running
make serve     # uvicorn server.app:app --port 8000
```

Then open `http://localhost:8000`. Expect an empty timeline for the first ~30 seconds while the first fragments land.

`make stream` is optional: with only `make serve` running, the Start button (section 6.5) spawns the agent. Do not run both — `start` will refuse, and the externally-started agent cannot be stopped from the UI.

---

## 9. IAM

Minimum policy for the single stream:

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

---

## 10. Acceptance criteria

1. `make stream` runs for 10 minutes without intervention; the archive contains ~10 minutes of footage, not 10 seconds. **This is the pacing check — verify it first.**
2. `GET /api/fragments` for the last hour returns multiple runs separated by sub-second gaps at loop boundaries.
3. Timeline renders those runs with visible gaps at the correct positions.
4. Clicking a bar plays footage from approximately that moment (±2s).
5. Clicking a gap does nothing and issues no request.
6. Killing the edge agent for 60s and restarting it produces a visible 60-second gap in the timeline within one poll cycle.
7. `GET /api/hls` with a 3600-second range returns 400 naming the chunk limit.
8. `GET /api/hls` for a range inside a known gap returns 404 with a readable message.
9. No AWS credential appears in any network response or in page source.
10. The whole thing is under ~600 lines excluding HTML/CSS.
11. Pressing Stop leaves **zero** containers running (`docker ps`) and no further fragment writes. Verify by log, not by the UI — the UI reporting "stopped" is exactly the symptom an orphan hides behind.
12. Start → Stop → Start cycles cleanly three times, with no `name already in use` errors and no accumulated containers.
13. Pressing Stop on an externally-started agent returns 409 and leaves it running.

**Status of #10:** currently **not met** — ~805 code lines excluding comments and blanks (~1023 raw). The overage is the Docker route, the recording supervisor, and the preflight fallbacks, none of which were in the original scope. Recorded here rather than quietly dropped: the budget existed to keep this readable, and that pressure still applies to anything added next.

---

## 11. Known failure modes

These will occur during the build. Each has a specific cause.

| Symptom | Cause |
|---|---|
| Archive covers seconds instead of minutes | `identity sync=true` missing or misplaced |
| `no element "kvssink"` | Producer SDK not built, or `GST_PLUGIN_PATH` unset |
| Timeline and player disagree by 2–5s | `ServerTimestamp` used in one endpoint, `ProducerTimestamp` in the other |
| HLS request fails on a wide range | ON_DEMAND caps fragments per session — keep chunks ≤ 5 min |
| Opaque 4xx from `get_hls_streaming_session_url` | Requested range contains no fragments |
| Timeline empty despite agent running | Fragments need ~10–30s to become listable; also check region mismatch between agent and server |
| Playback stalls at chunk boundary | Expected in this MVP — chunks are discrete, no rollover |
| Still recording after Stop; UI says stopped | Orphaned container. Killing `docker run` does not stop the container it started — remove it by name (section 4.3) |
| `start` refuses forever; wrong pid reported | `pgrep -f looper.py` matched `make stream`'s `sh -c` wrapper instead of the interpreter (section 5.5) |
| `docker run` fails: `name already in use` | Previous container not removed before the next loop iteration |
| Edited JS/CSS doesn't take effect | Static files served without `Cache-Control`; browsers cache them heuristically and there are no hashed filenames (section 12) |
| `cc1plus` killed / `cannot allocate memory` building kvssink | Base image's GCC too old, or one compile job per core on a memory-limited Docker VM (section 12) |
| Agent dies hours in, gap never recovers | Temporary SSO credentials expired; use a long-lived IAM user (section 3) |

---

## 12. Build notes

- `kvssink` is the friction point. It is not in stock GStreamer; it requires building `amazon-kinesis-video-streams-producer-sdk-cpp` and exporting `GST_PLUGIN_PATH`. `README.md` documents this for macOS (Homebrew GStreamer) and Debian/Ubuntu.
- **There is no AWS-published prebuilt image** — only a sample Dockerfile. `docker/kvssink/Dockerfile` builds one here. Two departures from AWS's sample are load-bearing and should not be "simplified" back:
  - **Ubuntu 22.04, not `amazonlinux:2`.** The latter ships GCC 7.3.1, which OOM-kills `cc1plus` compiling liblog4cplus even with ample memory free. Symptom is `cannot allocate memory`, which misleadingly reads as a resource limit — it is a toolchain problem.
  - **`-DBUILD_DEPENDENCIES=OFF`,** with log4cplus / OpenSSL / curl from apt. The dependency that fails is then linked, not compiled.
  - `make -j` is capped by a `BUILD_JOBS` arg (default 2), not `$(nproc)`. Docker Desktop reports every host core while getting a fraction of host RAM; budget ~1.5GB per job.
  - The image also carries `x264enc`, which generates a synthetic test clip when the host has no ffmpeg — useful because the alternative is making people install a media toolchain just to produce a 60-second test file.
- Do not introduce a database, a job queue, a Docker Compose file, or a frontend build step. If a piece of state seems to need persisting, it does not. (The recording supervisor's process handle is the one sanctioned exception — section 5.5.)
- Do not proxy video through FastAPI. The browser talks to AWS directly for media segments; the backend only mints URLs.
- Serve `web/` with `Cache-Control: no-cache`. Without it browsers apply heuristic caching to `app.js`/`style.css` and silently serve stale copies after an edit; with no build step there are no content-hashed filenames to fall back on. ETags keep revalidation a cheap 304.
- Prefer explicit code over clever code — this is a reference implementation meant to be read.
