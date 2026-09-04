# Module: Building the Cloud VMS Project

This is the on-ramp for the course's final project: a simple Video Management System (VMS) — a simulated camera streaming to Amazon Kinesis Video Streams, with a web interface for browsing and replaying archived footage. The full brief lives in [`module-design.md`](module-design.md) — worth skimming once now for orientation, and returning to properly once this module is done.

That project has three moving parts: an **edge** component (feeds video in, and must survive crashes and shut down cleanly), a **web/backend** component (FastAPI + Pydantic + Uvicorn — the credential boundary between the browser and AWS), and a **frontend** (plain HTML/JS + hls.js). This course builds the skill for each piece separately before wiring them together — starting with the web layer, since it's the one every other piece talks through, then the edge layer, since it's the one with the most operational subtlety — and finishes by assembling all of it into one running system.

Two conventions run through every lesson. **Every step produces a result you can see** — a process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — rather than code taken on trust. And **each lesson replaces a stand-in from the one before**: `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. That's what keeps you never more than one layer away from something you can verify.

## Who this is for

Students who already know Python fundamentals (functions, classes, running scripts, basic file I/O) but have not built a web application before. No prior HTTP, REST, async, or operating-systems (processes/signals) experience assumed — everything is introduced from first principles.

## Lessons

### Module 1 — Web Basics (FastAPI, Pydantic, Uvicorn)

| # | Lesson | You'll be able to... |
|---|---|---|
| 1 | [Your First FastAPI App](01-your-first-fastapi-app.md) | Explain the client/server model; install and run a minimal FastAPI + Uvicorn app; use `/docs`. |
| 2 | [Routes, Path & Query Parameters](02-routes-path-and-query-parameters.md) | Accept typed input from the URL; return lists and nested JSON; choose status codes deliberately. |
| 3 | [Validating Data with Pydantic](03-validating-data-with-pydantic.md) | Define `BaseModel`s; accept and validate a JSON request body; control response shape with `response_model`. |
| 4 (capstone) | [Mini Recording-Status API](04-capstone-mini-recording-status-api.md) | Build a small stateful GET/POST/POST group with correct idempotency and a `409 Conflict` — the same shape as the real project's recording controller, but with fake process state. |

### Module 2 — The Edge Agent (process supervision & signals)

| # | Lesson | You'll be able to... |
|---|---|---|
| 5 | [Supervising a Long-Running Process](05-edge-looper-process-supervision.md) | Spawn and supervise a real child process with `subprocess.Popen`; handle `SIGINT`/`SIGTERM` for clean shutdown; detect a crash vs. a signal kill; restart with exponential backoff. Replaces Lesson 4's fake `pid` with a real one. |

### Module 3 — Integration (Web meets Edge)

| # | Lesson | You'll be able to... |
|---|---|---|
| 6 | [Wiring It Together](06-wiring-recording-to-a-real-process.md) | Replace Lesson 4's fake `_state` with a real `subprocess.Popen`; detect an externally-started process safely (and know exactly why the naive way isn't safe); implement the real `SIGTERM`→`SIGKILL` stop policy; isolate the child from the server's own process group; build a minimal Start/Stop page that reflects real server state. |

### Module 4 — SDKs & Containers

| # | Lesson | You'll be able to... |
|---|---|---|
| 7 | [What Is an SDK, and Why Docker?](07-sdks-and-docker-basics.md) | Explain the difference between a pip-installable SDK and a compiled/native one; explain what a container is (and isn't); write a `Dockerfile`, build an image, run/inspect/stop a container; pass config and data in via env vars and volumes without baking either into the image. |
| 8 | [Dockerizing the Edge Agent](08-dockerizing-the-edge-agent.md) | Extend Lesson 5's `looper.py` with a `KVS_DOCKER_IMAGE` toggle; explain precisely why killing a `docker run` client can orphan its container (and when it doesn't); use a deterministic name + `docker rm -f` before every launch; forward credentials by variable name only; read the real `kvssink` Dockerfile as reference material. |

### Module 5 — GStreamer & Media Pipelines

| # | Lesson | You'll be able to... |
|---|---|---|
| 9 | [GStreamer Fundamentals](09-gstreamer-fundamentals.md) | Read and write `gst-launch-1.0`'s pipeline syntax; distinguish source/filter/sink elements; use `-v` and `gst-inspect-1.0`; demux and re-parse a real H.264 file with zero AWS dependency. |
| 10 | [Deconstructing the Real Pipeline](10-deconstructing-the-real-pipeline.md) | Read the complete real pipeline including `identity sync=true` and `kvssink`; demonstrate the pacing problem empirically; explain why the pipeline loops by restarting the whole process (reusing Lesson 5/8's supervisor unchanged); build `pipeline.py`. `kvssink` itself is an optional capstone, not required to finish the module. |

### Module 6 — AWS & boto3

| # | Lesson | You'll be able to... |
|---|---|---|
| 11 | [boto3 Fundamentals and the KVS Client](11-boto3-fundamentals-and-the-kvs-client.md) | Explain boto3's credential resolution order; run a permission-free smoke test; catch `ClientError` and branch on `e.response["Error"]["Code"]`; explain KVS's control-plane/data-plane split; build and verify `kvs.py`'s cached `archived_client()` factory. |
| 12 | [`/api/fragments` and `/api/hls`: The Real Endpoints](12-fragments-and-hls-the-real-endpoints.md) | Paginate `list_fragments` to exhaustion; merge fragments into contiguous runs using the project's exact gap rule; convert AWS `datetime`s to epoch floats at one boundary in `models.py`; implement `/api/hls`'s bounds-then-existence validation order; translate `ResourceNotFoundException` into a 404. |

### Module 7 — The Frontend & Assembly

| # | Lesson | You'll be able to... |
|---|---|---|
| 13 | [Assembling the Server](13-assembling-the-server.md) | Lay out the real project; load one `.env` from both processes without ever touching a credential in code; write the minimum IAM policy; write an idempotent provisioning script and a preflight that prints fixes instead of tracebacks; wire all five routes plus the static frontend into one `app.py` — and know why the mount goes last. |
| 14 | [The Timeline](14-the-timeline.md) | Build the sliding window and the time↔position conversion the whole interface rests on; render runs as bars with gaps as background; handle the four geometry edge cases real data produces; draw a ruler on real clock minutes; implement the empty/loading/error states and the `[hidden]` trap; test all the arithmetic without a browser. |
| 15 | [Playback, Recording Control & the Live System](15-playback-recording-control-and-the-live-system.md) | Turn a click into a validated chunk; play HLS with hls.js without proxying video through your server; track the playhead against real playback; drive a Start/Stop button entirely from server responses including the 409; then run the whole system against the spec's thirteen acceptance criteria. |

Each lesson is self-contained but builds on the previous one's code — work through a module's lessons in order, in the same project folder, rather than skipping around. Modules 2 and 3 each start a fresh project folder; Module 4's Lesson 7 starts another, and Lesson 8 returns to Lesson 5's folder to extend it. Module 5's Lesson 9 starts a fresh folder (just `clip.mp4` and `ffmpeg`/`gst-launch-1.0` output); Lesson 10 returns to Lesson 5/8's `looper.py` again. Module 6's Lesson 11 starts a fresh folder (`whoami.py`, then `kvs.py`); Lesson 12 extends it with `models.py` and the two real routes. Module 7 ends the pattern: Lesson 13 creates the real repository layout and moves every earlier module's code into it, and Lessons 14–15 work in that one tree from then on.

## How this maps to the real project

Once these modules are done, the reference spec's implementation will read as *familiar*, not new:

- `server/models.py` — Pydantic response models, exactly Lesson 3.
- `server/app.py`'s `GET /api/fragments` and `GET /api/hls` — typed query parameters and deliberate status codes (400, 404), exactly Lesson 2, applied to real AWS data instead of an in-memory list.
- `edge/looper.py` — exactly Lesson 5's supervisor, with Lesson 8's `KVS_DOCKER_IMAGE` toggle, now restarting Lesson 10's real pipeline instead of a dummy tick loop.
- `edge/pipeline.py` — exactly Lesson 10's `build_pipeline_argv`.
- `server/recording.py` and `web/app.js`'s recording controls — exactly Lesson 6, aimed at the real pipeline now that Module 5 has replaced `camera_sim.py`.
- `docker/kvssink/Dockerfile` — Lesson 8's annotated read-through, matching the spec's own build notes line for line.
- `server/kvs.py` — exactly Lesson 11's `archived_client()`.
- `server/app.py`'s `GET /api/fragments` and `GET /api/hls` bodies — exactly Lesson 12, now with real AWS calls behind the typed routes and deliberate status codes Lesson 2 introduced.
- `server/models.py`'s timestamp conversion — exactly Lesson 12's `to_epoch`/`from_epoch` boundary.
- `server/app.py` as a whole, `server/config.py`, `scripts/create_stream.py`, `scripts/check_env.py`, the Makefile and the IAM policy — Lesson 13.
- `web/index.html`, `web/style.css`, `web/app.js` — Lessons 14 and 15.

By the end of Module 7 there is nothing left to map: the lessons have built the whole reference implementation, and Lesson 15 closes by running it against the spec's own acceptance criteria.

## Running any lesson's code

Module 1 (Lessons 1–4) uses this skeleton:

```bash
mkdir fastapi-intro && cd fastapi-intro
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install fastapi uvicorn
# edit main.py per the lesson
uvicorn main:app --reload
```

Then work through the lesson against `http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

Module 2 (Lesson 5) needs no packages at all — `subprocess` and `signal` are Python standard library. Just:

```bash
mkdir process-supervision && cd process-supervision
# add camera_sim.py and looper.py per the lesson
python3 looper.py
```

A second terminal window, in the same folder, is required for several of Lesson 5's exercises — you'll be sending it signals from outside.

Module 3 (Lesson 6) is back to needing `fastapi` and `uvicorn` (same venv setup as Module 1) — it's where the web layer and the process-supervision layer combine. A second terminal and a browser are both required throughout.

Module 4 needs Docker Desktop (or Docker Engine) installed and running — `docker version` should print both a Client and Server section. Lesson 7 starts a fresh folder (`camera_sim.py` + a `Dockerfile`); Lesson 8 goes back to Lesson 5's `looper.py` and extends it. A second terminal is required throughout both lessons.

Module 5 needs GStreamer (`gst-launch-1.0 --version` to check) and `ffmpeg`. Lesson 9 starts a fresh folder; Lesson 10 goes back to Lesson 5/8's `looper.py` again. Actually publishing to a real Kinesis Video Stream (Lesson 10's optional capstone) additionally needs the real `kvssink` build from Lesson 8 (or a native install) and real AWS credentials — everything else in the module runs with neither.

Module 7 is where the separate folders stop existing: Lesson 13 assembles the real repository layout and everything after it runs from there, via `make setup` / `make serve`. It needs everything the earlier modules needed, plus a browser. Lessons 14 and 15 can be built and verified against `VMS_FIXTURES=1` (Lesson 13, Step 7) without any footage at all; seeing real video play additionally needs the `kvssink` capstone from Lesson 10.

Module 6 needs `boto3` (`pip install boto3`, same venv pattern as Module 1) and a real AWS account with an IAM user or role that has read access to Kinesis Video Streams, plus a stream with at least a few minutes of archived footage for the optional live-testing steps. Every piece of real logic in both lessons — pagination, the merge rule, the validation order — is also verified with fake-object tests that need neither boto3 nor a live stream, so the module is still fully workable without AWS access; only the "confirm it against a real stream" steps require it.

## A note on scope

These lessons deliberately never introduce a database, authentication, or async/await — none of those are needed for the actual final project (see its "explicit non-goals" section), and introducing them here would teach concepts this course never asks students to use. If a lesson feels like it's avoiding a "more proper" way of doing something, that's usually why.
