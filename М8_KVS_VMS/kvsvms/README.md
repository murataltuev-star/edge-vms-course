# KVS-VMS — the М8 project, whole

Lessons 2–6 assembled into the tree Lesson 6 lays out, with the finished
frontend from Lessons 7 and 8 beside it (moved from `reference/web/`). This is
the cloud VMS the whole course starts from — and, built as an image, it is
the agent М9's appliance runs.

```
kvsvms/
├── .env.example              L6 — one .env for both processes; config reads settings, never credentials
├── Makefile                  L6 — setup / stream / serve / test / clip / agent-image
├── iam-policy.json           L6 — six actions, one stream, scoped by ARN
├── edge/
│   ├── looper.py             L2 + L3 + L4 — the supervisor; backoff; docker wrapping; stale-container removal
│   ├── camera_sim.py         L2 — the dummy workload (CHILD=camera_sim runs without GStreamer)
│   ├── pipeline.py           L4 — the argv as a list; + М9's spool pipeline; + the upload pipeline
│   └── upload_segment.py     NEW — the acknowledged uploader М9 Lesson 4 needed (see below)
├── server/
│   ├── app.py                L6 — five routes + /health, then the static mount LAST
│   ├── config.py             L6
│   ├── kvs.py                L5 — the caching client factory
│   ├── fragments.py          L5 — pagination to exhaustion; the merge rule
│   ├── models.py             L5 — the timestamp boundary
│   ├── recording.py          L2  — the only code that knows a subprocess exists
│   └── fixtures.py           L6 — VMS_FIXTURES=1, off by default
├── scripts/
│   ├── create_stream.py      L6 — idempotent provisioning
│   ├── check_env.py          L6 — a fix, not a traceback
│   └── make_clip.sh          L4  — the 60-second test clip
├── web/                      L7–8 — index.html, style.css, app.js
├── docker/
│   ├── kvssink/Dockerfile    L3 — the SDK build (20–40 minutes, once)
│   └── vms-agent/Containerfile   NEW — localhost/example/vms-agent:1.0, what М9 runs
└── tests/                    the lessons' fake-client checks, runnable: python3 tests/run.py
```

## Running it (Lesson 6, Steps 8–9)

```bash
cp .env.example .env            # fill in AWS keys; attach iam-policy.json to that user
make clip                       # media/clip.mp4
make setup                      # venv, deps, preflight, stream provisioning
make serve                      # http://127.0.0.1:8000 — the timeline
VMS_FIXTURES=1 make serve       # four runs, three gaps, no AWS needed for the frontend
```

Then the six checks from Lesson 6, Step 9 — the mount-order one is check 3.

## What М9 needed from М8, and now has

`edgevms/quadlet/vms-agent.container` runs `localhost/example/vms-agent:1.0`
and assumed three things that did not exist. They do now:

1. **The image.** `docker/vms-agent/Containerfile` layers python, `server/`,
   `edge/`, `web/` and М9's `spool.py` onto the Lesson 3 kvssink image.
   `make agent-image` builds it from the course root.
2. **`GET /health` on port 8000** — row 2 of М9 Lesson 3's health check ("the
   VMS answers"). It answers *only* that; whether footage is being written is
   row 3, answered by the spool or by М10's Node, never by this route.
3. **`vms-upload-segment`** — the spool's `--upload-cmd`, which must exit 0
   only once the far side has acknowledged. `kvssink` cannot be it, because a
   sink's success is "the write returned". `edge/upload_segment.py` is:

   - a `filesrc ! qtdemux ! h264parse ! kvssink` re-publish of the closed
     segment, with `streaming-type=offline` (kvssink waits for persistence
     before EOS completes) and `file-start-time=` the segment's **original**
     start — so the archive shows footage at capture time, not at the moment
     the uplink came back. No `identity sync=true`: a backlog drains at line
     rate, bounded by the spool's `--budget`, not by the clock;
   - followed by `ListFragments` over the segment's own span. No fragment,
     no acknowledgement, exit 1, the file stays in the spool.

   The start time is the file's mtime (its close) minus its duration
   (ffprobe when present, `SEGMENT_SECONDS` otherwise) — reliable because a
   restarted pipeline never resumes a segment.

On the appliance the pipeline itself changes by one sink: with
`VMS_SPOOL_DIR` set, `looper.py` runs `build_spool_pipeline_argv` —
`splitmuxsink` into `/data/spool/<stream>/<launch-epoch>-<n>.mp4` — instead
of `kvssink`. Same source, same parse, same supervisor; М9 Lesson 4's "one
media-layer change" is one `if` in `_child_argv()`.

## One correction to Lesson 2, found by testing it

Lesson 2's positional matcher checks that the script name is the token right
after the interpreter — `tokens[1]` — and says that rules out editors. It
doesn't: `vim looper.py` is two tokens with the script second, and matches.
`recording._scan_ps` additionally requires `tokens[0]` to be a `python*`
binary; `tests/test_recording.py` has `vim`, `less`, `tail -f` and `grep` in
its fake process table, and only the interpreter line is found.

## What was verified where

- **Run, output real:** `tests/run.py` — 17 tests: pagination and the merge
  rule with the lesson's enforcing fake client; the HLS validation order; one
  `CreateStream` across two runs and AccessDenied propagating; the endpoint
  cache resolving each API name once; the positional process match; the
  pipeline and spool argvs; docker wrapping with credentials by name only and
  the value absent from the argv; and the uploader's decision — acknowledged
  only when a fragment lands in the segment's span, never asked when the
  pipeline failed. `server/app.py` imports with its routes in the right order
  (`/api/*`, `/health`, then the mount).
- **Written to the documentation, not executed here:** anything that touches
  AWS, GStreamer or Docker — `kvs.py` against a real endpoint, the three
  pipelines, `create_stream.py` and `check_env.py` live, both Containerfiles,
  and `upload_segment.py`'s two kvssink properties (`streaming-type=offline`,
  `file-start-time`), which are taken from the producer SDK's file-uploader
  sample and belong on the bench first. The sandbox had no boto3, no
  fastapi and no PyPI; the pure logic ran, the plumbing compiled.

## Known gaps, named

- `upload_segment.py` acknowledges on *any* fragment inside the span, not on
  the segment's full duration. A partial upload that persisted its first
  fragment is acknowledged and its file deleted. Tightening it means
  comparing the fragments' total length against the segment's — М9 Lesson 4
  exercise 3, in the other direction.
- `TIMELINE_WINDOW_MINUTES` and `PLAYBACK_CHUNK_SECONDS` are still
  duplicated into `web/app.js`, as Lesson 6 says and for the reason it gives.
- The agent image's `CMD` is the server; on the appliance recording is
  started through `POST /api/recording/start` or the Start button, exactly as
  in М8. An appliance that should record from boot needs one more line in
  `vms-agent.container` (`Exec=` a tiny start-on-boot wrapper) — left for
  М10, where `INSERT INTO cameras` replaces the button.
