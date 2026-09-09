# Module: Building the Cloud VMS Project

This is the on-ramp for the course's final project: a simple Video Management System (VMS) — a simulated camera streaming to Amazon Kinesis Video Streams, with a web interface for browsing and replaying archived footage. The full brief lives in [`module-design.md`](module-design.md) — worth skimming once now for orientation, and returning to properly once this module is done.

That project has three moving parts: an **edge** component (feeds video in, and must survive crashes and shut down cleanly), a **web/backend** component (FastAPI + Pydantic + Uvicorn — the credential boundary between the browser and AWS), and a **frontend** (plain HTML/JS + hls.js). This course builds the skill for each piece separately before wiring them together — starting with the web layer, since it's the one every other piece talks through, then the edge layer, since it's the one with the most operational subtlety — and finishes by assembling all of it into one running system.

Two conventions run through every lesson. **Every step produces a result you can see** — a process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — rather than code taken on trust. And **each lesson replaces a stand-in from the one before**: `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. That's what keeps you never more than one layer away from something you can verify.

## Who this is for

Students who already know Python fundamentals (functions, classes, running scripts, basic file I/O) but have not built a web application before. No prior HTTP, REST, async, or operating-systems (processes/signals) experience assumed — everything is introduced from first principles.

## Lessons

Eight lessons. The first five are multi-part — each part was once a lesson of its own, and each part still ends with its own troubleshooting table, recap and exercises — and the last three are the assembly, unchanged.

| # | Lesson | Parts | You'll be able to... |
|---|---|---|---|
| 1 | [Web Basics — FastAPI, Routes, Pydantic, and a Recording-Status API](01-web-basics-fastapi-pydantic-and-a-recording-api.md) | A · your first FastAPI app<br>B · routes, path and query parameters<br>C · validating data with Pydantic<br>D · a mini recording-status API | Explain the client/server model; run a minimal FastAPI + Uvicorn app and use `/docs`; accept typed input from the URL and choose status codes deliberately; define `BaseModel`s and control response shape; build a stateful GET/POST/POST group with correct idempotency and a `409 Conflict` — the same shape as the real project's recording controller. |
| 2 | [The Edge Agent — Process Supervision, and Wiring It to the Web Layer](02-the-edge-agent-process-supervision-and-the-web-layer.md) | A · supervising a long-running process<br>B · a real process behind the recording button | Spawn and supervise a real child process; tell `SIGINT`, `SIGTERM` and `SIGKILL` apart and know which you can catch; restart on crash with exponential backoff; replace the fake `_state` with a `subprocess.Popen` handle owned by one module; detect a process you did not start by *position*, not presence; drive a Start/Stop button entirely from server-reported state. |
| 3 | [SDKs and Containers — Dockerizing the Edge Agent](03-sdks-and-containers-dockerizing-the-edge-agent.md) | A · SDKs and Docker basics<br>B · Dockerizing the edge agent | Say what an SDK and a container actually are; build, run and inspect an image for `camera_sim.py`; pass configuration and data without baking either into the image; add a `KVS_DOCKER_IMAGE` toggle to `looper.py`; reproduce the orphaned-container failure and fix it with `docker rm -f` before every launch; forward credentials by name only; read the real `kvssink` Dockerfile. |
| 4 | [GStreamer — Fundamentals, and the Real Pipeline Deconstructed](04-gstreamer-fundamentals-and-the-real-pipeline.md) | A · GStreamer fundamentals<br>B · deconstructing the real pipeline | Build pipelines from sources, filters and sinks; read pads and caps; use `gst-inspect-1.0`; demux and re-parse a real file locally; read the project's exact pipeline element by element; demonstrate the pacing problem with a stopwatch; write `pipeline.py` as an argv list, never a shell string. |
| 5 | [boto3 and the KVS Client — Fragments and HLS, the Real Endpoints](05-boto3-the-kvs-client-fragments-and-hls.md) | A · boto3 fundamentals and the KVS client<br>B · `/api/fragments` and `/api/hls` | Prove credentials with `sts`; tell the control plane from the data plane and resolve an endpoint per API; read `ClientError` by code; build the caching client factory and verify it with fakes; paginate `list_fragments` to exhaustion; merge fragments into runs with the exact gap rule; implement `/api/hls`'s bounds-then-existence validation order. |
| 6 | [Assembling the Server](06-assembling-the-server.md) | — | Lay out the real project; load one `.env` from both processes without ever touching a credential in code; write the minimum IAM policy; write an idempotent provisioning script and a preflight that prints fixes instead of tracebacks; wire all five routes plus the static frontend into one `app.py` — and know why the mount goes last. |
| 7 | [The Timeline](07-the-timeline.md) | — | Build the sliding window and the time↔position conversion the whole interface rests on; render runs as bars with gaps as background; handle the four geometry edge cases real data produces; draw a ruler on real clock minutes; implement the empty, loading and error states. |
| 8 | [Playback, Recording Control & the Live System](08-playback-recording-control-and-the-live-system.md) | — | Turn a click into a validated chunk; play HLS with hls.js without proxying video through your server; track the playhead against real playback; drive a Start/Stop button entirely from server state; run the whole system against the spec's acceptance criteria. |

Each lesson builds on the previous one's code — work through the parts in order, in the same project folder. Lessons 1 and 2 each start a fresh folder; Lesson 3 Part A starts another, and Part B returns to Lesson 2's folder to extend `looper.py`. Lesson 4 Part A starts a fresh folder (just `clip.mp4` and `gst-launch-1.0` output); Part B returns to `looper.py` again. Lesson 5 Part A starts a fresh folder (`whoami.py`, then `kvs.py`); Part B extends it with `models.py` and the two real routes. Lesson 6 ends the pattern: it creates the real repository layout and moves every earlier lesson's code into it, and Lessons 7–8 work in that one tree from then on.

## How this maps to the real project

Once these modules are done, the reference spec's implementation will read as *familiar*, not new:

- `server/models.py` — Pydantic response models, exactly Lesson 1.
- `server/app.py`'s `GET /api/fragments` and `GET /api/hls` — typed query parameters and deliberate status codes (400, 404), exactly Lesson 1, applied to real AWS data instead of an in-memory list.
- `edge/looper.py` — exactly Lesson 2's supervisor, with Lesson 3's `KVS_DOCKER_IMAGE` toggle, now restarting Lesson 4's real pipeline instead of a dummy tick loop.
- `edge/pipeline.py` — exactly Lesson 4's `build_pipeline_argv`.
- `server/recording.py` and `web/app.js`'s recording controls — exactly Lesson 2, aimed at the real pipeline now that Lesson 4 has replaced `camera_sim.py`.
- `docker/kvssink/Dockerfile` — Lesson 3's annotated read-through, matching the spec's own build notes line for line.
- `server/kvs.py` — exactly Lesson 5's `archived_client()`.
- `server/app.py`'s `GET /api/fragments` and `GET /api/hls` bodies — exactly Lesson 5, now with real AWS calls behind the typed routes and deliberate status codes Lesson 1 introduced.
- `server/models.py`'s timestamp conversion — exactly Lesson 5's `to_epoch`/`from_epoch` boundary.
- `server/app.py` as a whole, `server/config.py`, `scripts/create_stream.py`, `scripts/check_env.py`, the Makefile and the IAM policy — Lesson 6.
- `web/index.html`, `web/style.css`, `web/app.js` — Lessons 7 and 8.

By the end of Lesson 8 there is nothing left to map: the lessons have built the whole reference implementation, and Lesson 8 closes by running it against the spec's own acceptance criteria.

The whole of it, assembled as Lesson 6 lays it out, is [`kvsvms/`](./kvsvms/README.md): `server/`, `edge/`, `scripts/`, `web/`, the IAM policy, the Makefile, the two Containerfiles, and the lessons' fake-client checks as a test suite (`python3 tests/run.py`, no AWS needed). It also carries the two things М9's appliance needed from this module and the lessons never wrote — a `/health` route, and `vms-upload-segment`, the spool uploader that exits 0 only once the archive holds the segment — and one correction to Lesson 2's process matcher that its own test found.

## Running any lesson's code

Lesson 1 uses this skeleton:

```bash
mkdir fastapi-intro && cd fastapi-intro
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install fastapi uvicorn
# edit main.py per the lesson
uvicorn main:app --reload
```

Then work through the lesson against `http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

Lesson 2 Part A needs no packages at all — `subprocess` and `signal` are Python standard library. Just:

```bash
mkdir process-supervision && cd process-supervision
# add camera_sim.py and looper.py per the lesson
python3 looper.py
```

A second terminal window, in the same folder, is required for several of Lesson 2's exercises — you'll be sending it signals from outside.

Lesson 2 Part B is back to needing `fastapi` and `uvicorn` (same venv setup as Lesson 1) — it's where the web layer and the process-supervision layer combine. A second terminal and a browser are both required throughout.

Lesson 3 needs Docker Desktop (or Docker Engine) installed and running — `docker version` should print both a Client and Server section. Part A starts a fresh folder (`camera_sim.py` + a `Dockerfile`); Part B goes back to Lesson 2's `looper.py` and extends it. A second terminal is required throughout both parts.

Lesson 4 needs GStreamer (`gst-launch-1.0 --version` to check) and `ffmpeg`. Part A starts a fresh folder; Part B goes back to `looper.py` again. Actually publishing to a real Kinesis Video Stream (Part B's optional capstone) additionally needs the real `kvssink` build from Lesson 3 (or a native install) and real AWS credentials — everything else in the module runs with neither.

Lesson 6 is where the separate folders stop existing: it assembles the real repository layout and everything after it runs from there, via `make setup` / `make serve`. It needs everything the earlier lessons needed, plus a browser. Lessons 7 and 8 can be built and verified against `VMS_FIXTURES=1` (Lesson 6, Step 7) without any footage at all; seeing real video play additionally needs the `kvssink` capstone from Lesson 4.

Lesson 5 needs `boto3` (`pip install boto3`, same venv pattern as Lesson 1) and a real AWS account with an IAM user or role that has read access to Kinesis Video Streams, plus a stream with at least a few minutes of archived footage for the optional live-testing steps. Every piece of real logic in both parts — pagination, the merge rule, the validation order — is also verified with fake-object tests that need neither boto3 nor a live stream, so the module is still fully workable without AWS access; only the "confirm it against a real stream" steps require it.

## A note on scope

These lessons deliberately never introduce a database, authentication, or async/await — none of those are needed for the actual final project (see its "explicit non-goals" section), and introducing them here would teach concepts this course never asks students to use. If a lesson feels like it's avoiding a "more proper" way of doing something, that's usually why.
