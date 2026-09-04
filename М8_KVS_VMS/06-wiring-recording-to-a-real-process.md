# Lesson 6 — Wiring It Together: A Real Process Behind the Recording Button

**Module:** Integration — the Web layer meets the Edge layer (Module 3)
**You will build:** a FastAPI app whose `/api/recording` endpoints control a real supervised process, plus a minimal web page with a working Start/Stop button.
**Time:** ~90–120 minutes.

## Why this lesson exists

Two lessons have been building toward this moment from opposite directions. Lesson 4 built `GET/POST /api/recording` against a fake `_state` dict with a made-up `pid`. Lesson 5 built a real supervised process — `subprocess.Popen`, real signals, a real `pid` — with no web framework anywhere near it. This lesson deletes the fake dict and replaces it with the real thing. When it's done, clicking a button in a browser will start an actual OS process, and the status text on the page will reflect what's actually running, not what the page hopes is running.

This is also where the real project's structure stops being an abstraction. You are about to write something extremely close to the actual `server/recording.py`, `server/app.py`'s recording routes, and a first pass at `web/index.html` / `web/app.js` — not a simplified stand-in for them.

## Prerequisites

- Lessons 1–5 completed. This lesson assumes fluency with FastAPI routes and Pydantic (1–3), the idempotency/`409` pattern (4), and `subprocess.Popen`/signals (5) — it builds on all of them without re-explaining.
- A second terminal window and a browser, both open throughout.

## Learning objectives

1. Replace fabricated process state with a real `subprocess.Popen` handle owned by a single module, called from FastAPI routes.
2. Detect a process this server didn't start by scanning `ps` output — and understand exactly why a naive version of that scan is unsafe.
3. Implement the real escalation policy: `SIGTERM`, wait, then `SIGKILL` if the process won't stop.
4. Isolate a managed child from signals sent to the server's own process group, so stopping the server doesn't silently kill (or fail to kill) the recording.
5. Build a minimal HTML/JS page that reflects server-reported state rather than its own optimistic guess.

---

## Step 1 — Project layout

New project folder, separate from the previous two:

```
recording-app/
├── camera_sim.py     # from Lesson 5, unchanged
├── recording.py       # NEW — owns the one real piece of server state
├── main.py             # FastAPI routes, thin
└── web/
    ├── index.html
    └── app.js
```

Copy `camera_sim.py` from Lesson 5 as-is — it already does everything needed: ticks forever, exits cleanly on `SIGTERM`. No changes to it in this lesson.

## Step 2 — `recording.py`: the one place that touches the process

This module plays the same role as the real project's `server/recording.py`: it is the *only* code that knows a subprocess exists. Routes in `main.py` will call three functions — `status()`, `start()`, `stop()` — and never touch `subprocess` directly.

Start with `status()` and `start()`, reusing Lesson 5's ideas directly:

```python
import subprocess
import sys

CHILD_SCRIPT = "camera_sim.py"

_current_proc: subprocess.Popen | None = None


def _reap_if_dead():
    """If our child exited on its own since we last checked, forget it."""
    global _current_proc
    if _current_proc is not None and _current_proc.poll() is not None:
        _current_proc = None


def status():
    _reap_if_dead()
    if _current_proc is not None:
        return {"running": True, "managed": True, "pid": _current_proc.pid}
    return {"running": False, "managed": False, "pid": None}


def start():
    global _current_proc
    current = status()
    if current["running"]:
        return current                              # idempotent — Lesson 4's rule, for real now
    _current_proc = subprocess.Popen([sys.executable, CHILD_SCRIPT])
    return status()
```

`_reap_if_dead` is new: Lesson 5's supervisor found out about a dead child because it was sitting in a blocking `.wait()` call. Here, nothing is blocking — a web request could arrive at any time, long after the child crashed on its own. `poll()` — new in this lesson — asks "has this process exited?" *without* blocking, returning `None` if it's still running or the exit code if it's not. Calling this at the top of `status()` means a crash between requests is discovered on the very next status check, not left showing a stale `running: true`.

## Step 3 — Detecting a process you didn't start (and a bug worth causing on purpose)

Lesson 4's `_simulate_external_start` faked the `managed: false` case with a debug route. Now that the process is real, you can detect it for real — but this is exactly where the real spec warns about a subtle trap (`pgrep -f` matching a shell wrapper instead of the real process), and it's worth understanding *why* rather than just avoiding the syntax it names.

**Write the tempting, wrong version first.** In a second terminal, start an "external" recording — imagine a teammate ran this directly, bypassing your API entirely:

```bash
python3 camera_sim.py &
```

Leave it running. Now, in a third terminal (or the same one, once it's backgrounded), try the classic one-liner for finding a process by name:

```bash
ps aux | grep camera_sim.py
```

You'll see **two** matching lines: the real `camera_sim.py` process — and `grep camera_sim.py` itself, because `grep`'s own command line contains the very text it was searching for. This is not a contrived edge case; it is *the* standard gotcha with `ps | grep` and its close relative `pgrep -f`, and it's why the real spec explicitly forbids `pgrep -f looper.py` for detecting the edge agent — a shell wrapper's command line can just as easily contain the search text as grep's own does, for the same underlying reason: **matching a search string anywhere in a full command line finds anything that happens to mention that text, not specifically "a process running that script."**

Now write `recording.py`'s detection function the naive way and see the same category of problem from Python instead of the shell:

```python
def _find_external_camera_pid_NAIVE():
    result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines()[1:]:
        pid_str, args = line.strip().split(maxsplit=1)
        if CHILD_SCRIPT in args:          # <-- looks reasonable. It isn't.
            return int(pid_str)
    return None
```

You didn't pipe through `grep` this time — so is it safe? No: `CHILD_SCRIPT in args` checks whether the substring `"camera_sim.py"` appears *anywhere* in that process's full command line. Anything that happens to mention the filename qualifies: someone editing the file (`vim camera_sim.py` shows up in `ps` with that text in its argv), a `tail -f camera_sim.py`, a teammate's shell history search — none of these are a running camera process, and all of them would be misreported as one.

### The fix: check position, not presence

```python
import os

def _find_external_camera_pid():
    """Scan the process table for a camera_sim.py process this server didn't start.

    Checks that CHILD_SCRIPT is specifically the argument immediately after
    the interpreter -- not merely present somewhere in the command line.
    That rules out anything whose argv happens to *mention* the filename
    (an editor, `tail -f`, a shell history match) without actually running
    it as the script.
    """
    result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_str, args = parts
        tokens = args.split()
        if len(tokens) < 2 or os.path.basename(tokens[1]) != CHILD_SCRIPT:
            continue
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if _current_proc is not None and pid == _current_proc.pid:
            continue                       # that's ours, already reported by status()
        return pid
    return None
```

`tokens[1]` is "whatever comes right after the interpreter" — for a process started as `python3 camera_sim.py`, that's `tokens[0] == "python3"` and `tokens[1] == "camera_sim.py"`, exactly the shape you're looking for. `os.path.basename(...)` handles the case where someone ran it as `python3 ./camera_sim.py` or with a full path. Nothing about *editing*, *viewing*, or *mentioning* the file produces that specific shape, because in each of those cases the filename is not immediately after the interpreter token.

Now wire it in:

```python
def status():
    _reap_if_dead()
    if _current_proc is not None:
        return {"running": True, "managed": True, "pid": _current_proc.pid}
    external_pid = _find_external_camera_pid()
    if external_pid is not None:
        return {"running": True, "managed": False, "pid": external_pid}
    return {"running": False, "managed": False, "pid": None}
```

Kill the external process from Step 3 for now (`kill -TERM` its pid) — you'll bring it back deliberately in Step 6's test walkthrough.

## Step 4 — `stop()`: refuse, or terminate-then-escalate

```python
class NotManaged(Exception):
    """Raised when the running recording wasn't started by this server."""


def stop():
    global _current_proc
    current = status()
    if not current["running"]:
        return current                     # idempotent no-op, same as Lesson 4
    if not current["managed"]:
        raise NotManaged()
    _current_proc.terminate()              # sends SIGTERM
    try:
        _current_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _current_proc.kill()               # SIGKILL — last resort
        _current_proc.wait()
    _current_proc = None
    return status()
```

This is Exercise 1 from Lesson 4, and Exercise 1 from Lesson 5, now actually implemented: a `409`-worthy refusal when the process isn't yours, and the real 15-second `SIGTERM`-then-`SIGKILL` policy from the spec. `NotManaged` is a plain Python exception, not an HTTP concept — deciding it means a `409` is `main.py`'s job, not this module's. Keeping that translation out of `recording.py` is what makes this module testable on its own, exactly as you did with plain function calls in Lessons 4 and 5, with no server running at all.

## Step 5 — One more real-world hazard: your own terminal

Start a recording (call `start()` from a `python3 -c` one-liner, or wait until Step 6 to do it through the API) and think about what happens when you later press `Ctrl+C` on the terminal running `uvicorn`. A terminal's `Ctrl+C` doesn't target one process — it sends `SIGINT` to the **entire foreground process group**. Unless told otherwise, a child spawned by `Popen` inherits its parent's process group, which means stopping your server this way sends `SIGINT` straight to the recording process too — simultaneously, out from under your own careful `stop()` logic, which never gets to run.

Whether that's catastrophic depends on luck: `camera_sim.py` only has a handler for `SIGTERM`, not `SIGINT`, so an unhandled `SIGINT` triggers Python's default behavior — an unhandled `KeyboardInterrupt`, a traceback, and exit. Untidy, but the process does stop. The real risk is the opposite mistake going unnoticed for a long time: code that *assumes* the child only ever stops through your own `stop()` function, when in fact a keystroke in the wrong terminal can kill it a different way entirely.

Fix it by giving the child its own process group:

```python
_current_proc = subprocess.Popen(
    [sys.executable, CHILD_SCRIPT],
    start_new_session=True,
)
```

`start_new_session=True` puts the child in a new session and process group of its own. A `Ctrl+C` in the terminal running your server now reaches only the server — the recording process is untouched, and stops only when your own `stop()` explicitly signals it. Update `start()` to include this argument.

> This one is worth more than reading about — Step 6 has you cause the *without* case on purpose and watch it happen, then confirm the fix.

## Step 6 — `main.py`: thin routes over `recording.py`

```python
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import recording

app = FastAPI()


class RecordingStatus(BaseModel):
    running: bool
    managed: bool
    pid: int | None


@app.get("/api/recording", response_model=RecordingStatus)
def get_recording_status():
    return recording.status()


@app.post("/api/recording/start", response_model=RecordingStatus)
def start_recording():
    return recording.start()


@app.post("/api/recording/stop", response_model=RecordingStatus)
def stop_recording():
    try:
        return recording.stop()
    except recording.NotManaged:
        raise HTTPException(
            status_code=409,
            detail="Recording is running but was not started by this server; stop it where it was started.",
        )


# Serve web/ as static files at "/" -- must come AFTER the /api/... routes above.
app.mount("/", StaticFiles(directory="web", html=True), name="web")
```

Two things worth noticing:

- Every route is two or three lines. All the actual logic lives in `recording.py` and was already written (and can be tested) without FastAPI in the picture at all — the same separation of concerns the real spec insists on: *"confined to `server/recording.py`... if this module starts growing a job queue or persisting anything, the design has gone wrong."*
- `app.mount(...)` is declared **last**, after the API routes. This is Lesson 2's route-ordering lesson again, one level up: a mount is matched in the order it's registered too, and a mount at `"/"` would happily swallow `/api/recording` if it were declared first. Explicit routes before a catch-all — same rule, new context.

`fastapi.staticfiles.StaticFiles` is new here: `directory="web", html=True` means requests to `/` serve `web/index.html`, and requests to `/app.js` serve `web/app.js` — a plain file server for anything that isn't one of your explicit API routes.

## Step 7 — The page and the button

`web/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>cam-01</title>
</head>
<body>
  <div>
    <strong>cam-01</strong>
    <span id="status-text">○ stopped</span>
    <button id="toggle-btn">Start</button>
  </div>
  <p id="message"></p>
  <script src="/app.js"></script>
</body>
</html>
```

`web/app.js` — plain JavaScript, no build step, no framework. If you haven't written JavaScript before: `fetch` is the browser's equivalent of Python's `requests`, and `async`/`await` here just means "wait for this network call before running the next line" — you're not expected to master JS in this lesson, only enough to test what you built.

```javascript
const statusText = document.getElementById("status-text");
const toggleBtn = document.getElementById("toggle-btn");
const messageEl = document.getElementById("message");

const POLL_MS = 3000;
let busy = false;   // true while a start/stop request is in flight

function render(state) {
  statusText.textContent = state.running ? "● recording" : "○ stopped";

  if (busy) return;  // don't clobber "Starting…"/"Stopping…" mid-request

  toggleBtn.textContent = state.running ? "Stop" : "Start";
  toggleBtn.disabled = false;
  toggleBtn.title = (state.running && !state.managed)
    ? "Started outside this app — stop it where it was started"
    : "";
}

async function fetchStatus() {
  const res = await fetch("/api/recording");
  render(await res.json());
}

async function toggle() {
  const wantStop = toggleBtn.textContent === "Stop";
  busy = true;
  toggleBtn.disabled = true;
  toggleBtn.textContent = wantStop ? "Stopping…" : "Starting…";
  messageEl.textContent = "";

  const res = await fetch(`/api/recording/${wantStop ? "stop" : "start"}`, { method: "POST" });
  if (res.status === 409) {
    const body = await res.json();
    messageEl.textContent = body.detail;
  }

  busy = false;
  await fetchStatus();   // always resync from the server -- never trust our own guess
}

toggleBtn.addEventListener("click", toggle);
fetchStatus();
setInterval(fetchStatus, POLL_MS);
```

The design rule worth naming explicitly, because it's easy to get backwards: **the button never decides what state it's in — the server does.** After every click, `toggle()` re-fetches real status from `/api/recording` rather than assuming the request it just made succeeded the way it expected. This is exactly the real spec's requirement: *"The button never trusts its own optimistic guess, so an agent that dies on its own — or is stopped in another terminal — corrects itself within one poll cycle."* The 3-second poll means even a change from a completely different terminal shows up here on its own, without a click.

This page is deliberately bare — no timeline, no video, none of the visual design work the real frontend module covers later. It exists to prove the wiring works, the same way Lesson 4 tested through `/docs` instead of a real page.

## Step 8 — Full test walkthrough

Run `uvicorn main:app --reload` and open `http://127.0.0.1:8000/`. Work through this exact sequence — it exercises every piece built above, in the order most likely to reveal a mistake:

1. **Start via the button.** Status flips to `● recording`, button becomes `Stop`. Confirm in a second terminal: `ps -eo pid,args | grep camera_sim.py` shows exactly one real process, whose pid matches what `GET /api/recording` reports.
2. **Click Start again** (or refresh and click before the first request would plausibly have failed). Still exactly one `camera_sim.py` process — idempotency, holding under a real process this time, not just a dict.
3. **Stop via the button.** Status flips back, the process disappears from `ps`.
4. **Simulate a teammate.** In the second terminal: `python3 camera_sim.py &`. Wait up to 3 seconds for the page to poll. Status shows `● recording` — detected via `_find_external_camera_pid`, not started by you. Hover the button: the tooltip explains it isn't yours to stop. Click **Stop** anyway: the page shows the `409`'s message instead of silently failing or crashing. Confirm via `ps` that the external process is untouched. Clean it up by hand: `kill -TERM` its pid in the second terminal.
5. **Cause Step 5's hazard, then fix it.** Temporarily remove `start_new_session=True` from `start()`, restart `uvicorn`, click Start, then press `Ctrl+C` on the terminal running `uvicorn`. Check `ps` — the recording process is gone too, killed by the same `Ctrl+C`, without your `stop()` logic ever running. Put `start_new_session=True` back, restart, repeat: click Start, `Ctrl+C` the server, check `ps` again — this time the recording process is still running, orphaned but alive, exactly as designed.
6. **The honest limitation.** With the orphaned process from step 5 still running, start `uvicorn` again and reload the page. Status shows `● recording` — but now `managed: false`, and the Stop button will `409` if you press it. The *new* server process has no memory of ever starting that recording; only real, ongoing process ownership survives a restart, not the fact of who originally started it. This isn't a bug to fix — it's a direct, honest consequence of tracking ownership in memory, the same limitation Lessons 4 and 5 both flagged for their own state. Stop the orphan by hand: `kill -TERM` its pid.

If every step above matches what's described, the web layer and the edge layer are now one working system.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ModuleNotFoundError: No module named 'recording'` | `main.py` and `recording.py` must be in the same folder, and you must run `uvicorn` from that folder. |
| `/` returns a raw file listing or a 404 instead of the page | Check `app.mount("/", StaticFiles(directory="web", html=True), ...)` is the *last* thing registered, and that `web/index.html` exists relative to where you run `uvicorn`. |
| Clicking Start does nothing visible | Open the browser's developer console for a JS error before assuming the backend is at fault — a typo in `app.js` fails silently in the page, not in your terminal. |
| Two `camera_sim.py` processes after clicking Start once | `start()` is missing its idempotency check (`if current["running"]: return current`) — re-check Step 2. |
| External-agent detection finds nothing even though `camera_sim.py &` is running | Confirm you're testing with the *positional* version from Step 3, not the naive one — and that you started it as `python3 camera_sim.py`, not via a wrapper script whose argv looks different. |
| `Stop` always 409s, even for a recording you started via the button | Check `start()` is actually storing the handle in `_current_proc` (a stray local variable instead of the module-level one is a common typo) — `status()` should show `managed: true` right after a button-driven start. |

## Recap

- `recording.py` is the single place that owns the real process handle — routes in `main.py` never touch `subprocess` directly, mirroring the real project's design intent.
- `poll()` is the non-blocking way to notice a child has exited between requests, versus Lesson 5's blocking `.wait()` inside a dedicated supervisor loop.
- Detecting "a process by name" via `ps` must check *position* (the script name immediately after the interpreter), not mere *presence* of the name anywhere in the command line — the same category of bug as `ps | grep` matching itself, and exactly why the real spec forbids `pgrep -f` for this.
- `terminate()` → `wait(timeout=...)` → `kill()` on `TimeoutExpired` is the real stop policy: ask nicely, then force it.
- `start_new_session=True` isolates a managed child from signals sent to the server's own process group — without it, stopping the server can silently kill (or corrupt the shutdown of) the process it's supposed to be managing separately.
- A page should reflect server-reported state after every action, never its own optimistic assumption about what just happened.
- Process *ownership* tracked only in memory does not survive a server restart, even though the process itself might — a real, unavoidable limitation, not a bug.

## Exercises

1. Add a `GET /api/recording/log` route (or similar) that tails the last N lines of the child's output — you'll need to redirect `camera_sim.py`'s stdout to a file in `start()` (`stdout=open("camera.log", "a")`) rather than letting it inherit the server's own stdout, since a real deployment can't assume someone is watching the terminal.
2. The naive substring matcher from Step 3 would also misfire if a student ever names an unrelated script `my_camera_sim.py.bak` — walk through *why* the positional fix handles that case correctly too, without changing a line.
3. Right now, restarting the server always demotes a previously self-started, still-running recording to `managed: false` (Step 8.6). Sketch, in comments, one way you could make ownership survive a restart (hint: what would you need to write to disk, and when, to reconstruct it later — and what new failure modes would that persistence itself introduce?). You don't need to implement it — the real spec deliberately keeps this in-memory-only; explain why that might be the right trade-off rather than an oversight.
4. Add a `RESTART_DELAY` constant and a `POST /api/recording/restart` route that calls `stop()` then `start()` — decide and justify: should this be idempotent the same way `start()` and `stop()` are individually, and what should happen if it's called while a `409`-worthy external recording is running?

## Where this is going

`camera_sim.py` has done its job — it let you build and test every piece of process ownership, signal handling, and web wiring without needing GStreamer, `kvssink`, or AWS credentials installed. The next module swaps it for the real pipeline from the project spec (`gst-launch-1.0 ... kvssink ...`) and introduces `boto3` for the two read endpoints (`GET /api/fragments`, `GET /api/hls`) that make the archive browsable — `recording.py`, `main.py`, and `app.js` above barely change shape when that happens; they gain neighbors, not replacements.
