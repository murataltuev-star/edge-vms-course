# Lesson 4 (Capstone) — A Mini Recording-Status API

**Module:** Web Basics for the Cloud VMS Project
**You will build:** an in-memory GET/POST API that starts, stops, and reports on a "recording," idempotently — the same shape as the real project's recording controller.
**Time:** ~75–90 minutes.

## Why this lesson exists

This is where Lessons 1–3 stop being separate ideas and become one working thing. It's also a preview: the real Cloud VMS backend you'll build later in this course has an endpoint group that looks almost exactly like what you're about to write —

```
GET  /api/recording        → {"running": bool, "managed": bool, "pid": int | null}
POST /api/recording/start  → same shape
POST /api/recording/stop   → same shape, or 409 if the recording wasn't started by this server
```

— except there it controls a real background process (a video pipeline). Here it controls a fake one: a plain Python variable pretending to be a running process. Everything about the *API shape*, the *validation*, and — most importantly — the *idempotency rule* is identical. Only the "does actual work" part is simplified away, on purpose, so you can focus entirely on the web layer.

## Prerequisites

- Lessons 1–3 completed. This lesson assumes you're fluent with routes, path/query parameters, `BaseModel`, and `response_model` — it won't re-explain them.

## Learning objectives

1. Model a small piece of *server-side state* correctly, and know why this is unusual compared to Lessons 1–3.
2. Implement an **idempotent** start operation — one that's safe to call more than once.
3. Return a `409 Conflict` deliberately, and explain why 409 and not 400 or 404.
4. Read and simulate the "who owns this" distinction the real project calls `managed`.
5. Test a stateful API by making a *sequence* of requests, not just one.

---

## Step 1 — Why state changes the rules

Every route in Lessons 1–3 either read from a fixed list or validated an incoming request in isolation. This lesson's routes must additionally remember something *between* requests: is the recording currently running? Who started it?

That's new, and worth pausing on: the real project's spec is explicit that this is **the one place the backend holds state**, precisely because it's unusual and needs to be contained deliberately rather than let spread. You're about to feel why, in miniature.

## Step 2 — Model the state and the response shape

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class RecordingStatus(BaseModel):
    running: bool
    managed: bool
    pid: int | None


# Module-level state: simulates "a handle to a running background process."
# In the real project this is a subprocess.Popen object; here it's just a dict.
_state = {"running": False, "managed": False, "pid": None}
_next_pid = 1000  # fake PIDs, just to make each simulated start look distinct
```

Notice `_state` lives at **module level**, outside any function. Every request handler reads and writes the same dict — this is what "the server remembers something" looks like in code. It also means: restart Uvicorn, and the state resets to `{"running": False, "managed": False, "pid": None}`, same as the in-memory `cameras` list in Lesson 2. That's fine for this exercise; the real project's equivalent state (a live process handle) *can't* survive a restart either, for the very same reason — there's nothing to reload it from.

## Step 3 — `GET /api/recording`

```python
@app.get("/api/recording", response_model=RecordingStatus)
def recording_status():
    return _state
```

Nothing new here — a `response_model`-typed read, exactly like Lesson 3. Run it and confirm: `{"running": false, "managed": false, "pid": null}`.

## Step 4 — `POST /api/recording/start`, made idempotent

The naive version:

```python
@app.post("/api/recording/start", response_model=RecordingStatus)
def start_recording_naive():
    global _next_pid
    _state["running"] = True
    _state["managed"] = True
    _state["pid"] = _next_pid
    _next_pid += 1
    return _state
```

Call this endpoint twice in a row through `/docs`. Watch the `pid` change on the second call. That's a bug: you now have a stale reference to a "process" your code has lost track of — in the real project this exact mistake means two publishers writing to the same video stream at once, corrupting the archive. **Starting something that's already started must not start a second one.**

The fix — check state before acting:

```python
@app.post("/api/recording/start", response_model=RecordingStatus)
def start_recording():
    global _next_pid
    if _state["running"]:
        return _state          # already running — report current state, do nothing
    _state["running"] = True
    _state["managed"] = True
    _state["pid"] = _next_pid
    _next_pid += 1
    return _state
```

Call it three times in a row now. The `pid` in the response should be identical every time after the first call. This is what **idempotent** means in practice: calling the operation once, or five times, leaves the system in the same state as calling it once. It's not automatic — you write the `if` check on purpose. Contrast this with `POST /cameras` from Lesson 3: calling that twice with the same body creates two separate resources. Not every `POST` should be idempotent; this particular one must be, because "start" describes a target state ("recording should be on"), not a request to create a new thing.

## Step 5 — `POST /api/recording/stop`, and the `managed` distinction

The real project makes a sharp distinction: a recording this server started (`managed: true`) is this server's to stop. A recording started some other way — someone running the pipeline directly in their own terminal — is *reported* but is **not this server's to kill**. Simulate that distinction:

```python
@app.post("/api/recording/stop", response_model=RecordingStatus)
def stop_recording():
    if not _state["running"]:
        return _state                          # already stopped — idempotent, same idea as start
    if not _state["managed"]:
        raise HTTPException(
            status_code=409,
            detail="Recording is running but was not started by this server; stop it where it was started.",
        )
    _state["running"] = False
    _state["managed"] = False
    _state["pid"] = None
    return _state
```

Two return paths and one error path, each deliberate:

- Not running at all → idempotent no-op, same reasoning as `start`.
- Running **and** managed by this server → actually stop it.
- Running but **not** managed by this server → refuse, with `409 Conflict`. Not `400` (the request itself isn't malformed) and not `404` (the resource exists, it's just not yours to change) — `409` specifically means *the request conflicts with the current state of the resource*, which is exactly this situation.

## Step 6 — Simulating "someone else started it"

There's no real external process here, so give yourself a way to simulate one — a small helper endpoint, clearly marked as test-only:

```python
@app.post("/api/recording/_simulate_external_start")
def simulate_external_start():
    """Test helper only: pretend a recording was started outside this server."""
    _state["running"] = True
    _state["managed"] = False
    _state["pid"] = 99999
    return _state
```

Now walk through the full scenario in `/docs`, in this exact order, checking the response each time:

1. `GET /api/recording` → `running: false`.
2. `POST /api/recording/start` → `running: true, managed: true`, some `pid`.
3. `POST /api/recording/start` again → identical response, same `pid` (idempotency, Step 4).
4. `POST /api/recording/stop` → `running: false, managed: false, pid: null`.
5. `POST /api/recording/_simulate_external_start` → `running: true, managed: false, pid: 99999`.
6. `POST /api/recording/stop` → **409**, with your explanatory message. The state must not change — confirm with a `GET /api/recording` right after.

If step 6 doesn't produce a 409, or if it does but the state changed anyway, you have a real bug — go back to Step 5's code before continuing.

## Step 7 — What's simplified, and what isn't

Be precise about the gap between this and the real thing, so you carry the right lesson forward:

| Here (toy) | Real project (later in this course) |
|---|---|
| `_state` is a plain `dict` | A `subprocess.Popen` handle to an actual GStreamer pipeline |
| "pid" is a fake incrementing counter | A real OS process ID |
| `_simulate_external_start` fakes external ownership | Detected by scanning real `ps` output for a matching command line |
| Stopping just flips a flag | Sends `SIGTERM`, waits up to 15s, escalates to `SIGKILL` if ignored |
| State always resets on restart | Same limitation — this is not a simplification, it's shared with the real system |

Everything in the left column is a stand-in for something with real-world consequences on the right. The **shape of the API, the idempotency rule, and the `managed` conflict logic are not simplified at all** — you just wrote the real design, against fake state. When you build the actual `server/recording.py` later, this lesson's code is structurally what you're extending, not throwing away.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `pid` changes on repeated `start` calls | The idempotency check (`if _state["running"]: return _state`) is missing or placed after the state mutation instead of before it. |
| `stop` after `_simulate_external_start` doesn't 409 | Check `_simulate_external_start` actually sets `managed: False`, and that `stop` checks `managed` before mutating state. |
| State doesn't reset between test runs and confuses you | That's `--reload` preserving the module-level dict across code edits within the same process; a full server restart (`Ctrl+C`, rerun `uvicorn`) resets it. |
| `NameError: global _next_pid` or similar | You need the `global` declaration inside any function that *reassigns* a module-level name (`_next_pid += 1`), not inside functions that only mutate a mutable value in place (`_state["running"] = True` doesn't need it). |

## Recap

- Server-side state (a module-level variable, here; a `Popen` handle, later) is the exception, not the rule — most of what you've built this module is stateless request/response.
- Idempotency for a "start" operation means checking current state before acting, so repeated calls are safe.
- `409 Conflict` is the correct status code for "the request is fine, but it conflicts with the resource's current state" — distinct from `400` (bad request) and `404` (no such resource).
- A `managed`-style ownership flag lets a server distinguish "things I'm responsible for" from "things I merely observe" — and refuse to act on the latter.
- Everything you built here about validation, response shape, and status codes was Lessons 1–3, unchanged; only the addition of state and idempotency was new.

## Exercises

1. Add a fourth field to `RecordingStatus`, `started_at: float | None`, populated with `time.time()` on a real start and cleared on stop. Confirm it survives the idempotent double-`start` case unchanged (it shouldn't reset on the second call).
2. Write a short Python script (using the `requests` library, `pip install requests`) that calls `start`, then `start` again, then `stop`, then `stop` again, printing each response — confirm programmatically what you confirmed by hand in Step 6.
3. Currently `_simulate_external_start` is a real route reachable by anyone. In one or two sentences, explain why a real project would never ship a route like this, and what you'd do instead to test the `managed: false` path safely (hint: think about what a proper *test suite*, run separately from the live server, would do instead).
4. The real spec says stopping should escalate from `SIGTERM` to `SIGKILL` after a 15-second wait. Sketch — in comments, no need to fully implement — how you'd adapt this lesson's `stop_recording` if `_state` held a real `subprocess.Popen` object instead of a dict.

---

## Module wrap-up

You now have, in your own hands, working examples of every piece the real Cloud VMS backend is built from: routes that read (Lesson 1), routes that take input safely via path and query parameters (Lesson 2), routes that validate structured input and shape structured output with Pydantic (Lesson 3), and a stateful pair of endpoints with a real idempotency and conflict-handling rule (this lesson) — but `_state["pid"]` here is still fake, and `stop_recording` still can't actually stop anything.

The next module closes exactly that gap: Lesson 5 builds a real supervised process — using `subprocess.Popen` and OS signals (`SIGINT`, `SIGTERM`, `SIGKILL`) — that this lesson's `_state` dict was always meant to stand in for. Once that lesson is done, the two halves combine: this API's `start`/`stop` will hold and control a real process handle instead of a dict.
