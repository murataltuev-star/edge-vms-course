# Lesson 2 — The Edge Agent — Process Supervision, and Wiring It to the Web Layer

**Module:** KVS-VMS — a cloud VMS on Kinesis Video Streams (Module 8)
**You will build:** a real supervisor that starts, monitors, restarts on crash and cleanly stops a second OS process — then a FastAPI app whose `/api/recording` endpoints control it, with a web page whose Start/Stop button never trusts its own guess.
**Time:** ~3–3.5 hours, in two parts.

> **This lesson is in 2 parts** — formerly Lessons 5–6 — and the step numbers run through all of them. Each part ends with its own troubleshooting table, recap and exercises; do the parts in order.

## Prerequisites

**Part A.**
- Lesson 1 completed. This lesson does not use FastAPI or Pydantic at all — it's plain Python and the standard library — but Lesson 1's idea of a `managed` process you are responsible for stopping is exactly what you're about to build for real.
- Comfortable with a second terminal window, since several exercises require sending signals to a running process from outside it.

**Part B.**
- Lesson 1 and Part A completed. This lesson assumes fluency with FastAPI routes and Pydantic (1–3), the idempotency/`409` pattern (4), and `subprocess.Popen`/signals (5) — it builds on all of them without re-explaining.
- A second terminal window and a browser, both open throughout.

## Learning objectives

1. Explain the parent/child process relationship and what `subprocess.Popen` actually does.
2. Distinguish `SIGINT`, `SIGTERM`, and `SIGKILL`, and know which ones your code can and cannot intercept.
3. Write a Python signal handler and understand when it runs relative to the rest of your code.
4. Build a supervisor loop that logs each run, restarts a crashed child with exponential backoff, and shuts down cleanly on request.
5. Read a `subprocess` return code correctly — including the "killed by a signal" case, which is not the same as "exited with an error."
6. Replace fabricated process state with a real `subprocess.Popen` handle owned by a single module, called from FastAPI routes.
7. Detect a process this server didn't start by scanning `ps` output — and understand exactly why a naive version of that scan is unsafe.
8. Implement the real escalation policy: `SIGTERM`, wait, then `SIGKILL` if the process won't stop.
9. Isolate a managed child from signals sent to the server's own process group, so stopping the server doesn't silently kill (or fail to kill) the recording.
10. Build a minimal HTML/JS page that reflects server-reported state rather than its own optimistic guess.

---

## Part A — Supervising a Long-Running Process: `edge/looper.py`


Lesson 1 ended with a `_state` dict pretending to hold a running process — a fake `pid`, a fake `managed` flag. That was deliberate: it let you focus entirely on the web layer. Now we build the thing that was being faked: a real, supervised, long-running child process — the actual shape of the real project's `edge/looper.py`, which supervises the GStreamer pipeline that publishes video.

We're not touching GStreamer, AWS, or `kvssink` yet — those come later. The child process for this lesson is deliberately trivial: **an infinite loop that ticks once a second**, standing in for "a pipeline that runs as long as recording is on." What you're learning here is not about video — it's the general skill of *supervising a process you don't fully control*: starting it, telling if it's still alive, restarting it if it dies unexpectedly, and shutting it down cleanly when asked. That skill transfers unchanged to the real pipeline, and to almost any long-running background job you'll ever run.
## Step 1 — What a subprocess actually is

Every process on your machine (except the very first one the OS starts at boot) has a **parent**. When your Python script calls `subprocess.Popen([...])`, the operating system creates a brand-new process — a *child* — that runs independently: it has its own memory, its own program counter, and it keeps running even if your Python code stops paying attention to it. Your script gets back a handle (a `Popen` object) that lets you ask questions about that child and send it instructions, but the child is not "inside" your script — it's a sibling process the OS is running alongside yours.

This matters immediately: if your Python process exits without doing anything about its child, **the child does not automatically stop**. It becomes an *orphan*, still running, adopted by the operating system's init process, invisible to whatever spawned it. You'll deliberately reproduce this failure mode in Step 7, because avoiding it by accident is not good enough — you need to know exactly what causes it.

## Step 2 — The dummy workload: `camera_sim.py`

Create a new project folder (separate from `fastapi-intro` — this lesson has no web server in it) and add `camera_sim.py`:

```python
#!/usr/bin/env python3
"""Dummy workload standing in for the real GStreamer pipeline.

Ticks once a second forever, and exits(1) after CRASH_AFTER seconds if that
env var is set, so the supervisor's crash-and-backoff path can be rehearsed
on demand instead of waiting for a real failure.
"""
import os
import signal
import sys
import time

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def main():
    crash_after = os.environ.get("CRASH_AFTER")
    crash_after = float(crash_after) if crash_after else None
    start = time.monotonic()
    tick = 0
    print(f"[camera] starting (pid={os.getpid()})", flush=True)
    while not _stop:
        time.sleep(1)
        tick += 1
        print(f"[camera] tick {tick}", flush=True)
        if crash_after is not None and (time.monotonic() - start) >= crash_after:
            print("[camera] simulated crash", flush=True)
            sys.exit(1)
    print("[camera] SIGTERM received, exiting cleanly", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
```

Run it on its own first: `python3 camera_sim.py`. You'll see it print a tick once a second, forever. Stop it with `Ctrl+C` for now — we'll come back to what that actually did.

Two details worth noticing before moving on:

- `signal.signal(signal.SIGTERM, _handle_sigterm)` registers a **handler** — a function the Python interpreter will call when the process receives a `SIGTERM`, instead of the default behavior (which is to terminate immediately). The handler just sets a flag; the main loop checks that flag once per second and exits its own way, printing a clean message first. This is the pattern for "let me finish what I'm doing, then stop" — the handler doesn't do the stopping itself, it *requests* it.
- `CRASH_AFTER` is a deliberate escape hatch. You will not wait around for a real bug to test your supervisor's crash-handling — you'll trigger a fake crash on demand.

## Step 3 — Three signals, three meanings

Before writing the supervisor, get these straight — the rest of the lesson assumes you have:

| Signal | Typically sent by | Can a Python program catch it? | Meaning |
|---|---|---|---|
| `SIGINT` | `Ctrl+C` in a terminal | Yes | "The user at the keyboard wants this to stop." |
| `SIGTERM` | `kill <pid>` (the default signal), Docker/systemd/your future FastAPI backend stopping a process it manages | Yes | "Please stop, at your own pace, cleanly." The polite request. |
| `SIGKILL` | `kill -9 <pid>` / `kill -KILL <pid>` | **No — never** | "Stop, immediately, no cleanup." The OS terminates the process directly; your code never runs another line. |

The uncatchable-ness of `SIGKILL` is not a Python limitation — it's enforced by the operating system kernel, on purpose, as a last resort for processes that are stuck or refusing to respond to `SIGTERM`. Nothing you write can intercept it. Keep that in the back of your mind for Step 7.

`camera_sim.py` above only handles `SIGTERM` explicitly — it doesn't need a `SIGINT` handler of its own, because it will always be started *by* the supervisor, never directly by a person at a keyboard pressing Ctrl+C in its own terminal. The supervisor, which you *do* run directly, is the one that needs to handle both.

## Step 4 — The supervisor: `looper.py`

```python
#!/usr/bin/env python3
import signal
import subprocess
import sys
import time
from datetime import datetime

CHILD_SCRIPT = "camera_sim.py"
BACKOFF_START = 1.0
BACKOFF_CAP = 30.0

_shutting_down = False
_current_proc = None


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}  {msg}", flush=True)


def _request_shutdown(signum, frame):
    global _shutting_down
    sig_name = signal.Signals(signum).name
    _log(f"received {sig_name}, shutting down")
    _shutting_down = True
    if _current_proc is not None and _current_proc.poll() is None:
        _current_proc.terminate()  # sends SIGTERM to the child


signal.signal(signal.SIGINT, _request_shutdown)
signal.signal(signal.SIGTERM, _request_shutdown)


def run_child_once():
    global _current_proc
    argv = [sys.executable, CHILD_SCRIPT]     # a list, never shell=True — see note below
    _current_proc = subprocess.Popen(argv)
    if _shutting_down:                        # shutdown requested in the instant between
        _current_proc.terminate()             # spawning and reaching this line — close the gap
    started = time.monotonic()
    _current_proc.wait()
    duration = time.monotonic() - started
    returncode = _current_proc.returncode
    _current_proc = None
    return returncode, duration


def main():
    backoff = BACKOFF_START
    loop_num = 0
    while not _shutting_down:
        loop_num += 1
        _log(f"loop {loop_num} started")
        returncode, duration = run_child_once()

        if _shutting_down:
            _log(f"loop {loop_num} stopped after {duration:.1f}s (shutdown)")
            break

        if returncode == 0:
            _log(f"loop {loop_num} exited cleanly after {duration:.1f}s, restarting")
            backoff = BACKOFF_START
            continue

        if returncode < 0:
            _log(f"loop {loop_num} killed by signal {-returncode} after {duration:.1f}s")
        else:
            _log(f"loop {loop_num} failed (exit {returncode}) after {duration:.1f}s")

        _log(f"backing off {backoff:.0f}s before retry")
        time.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_CAP)

    _log("stopped")


if __name__ == "__main__":
    main()
```

Walk through this deliberately, piece by piece — every line here earns its place.

### `argv` as a list, not a string

`[sys.executable, CHILD_SCRIPT]` is a **list** of separate arguments, passed to `Popen` without `shell=True`. This isn't a style preference. With `shell=True`, the string is handed to `/bin/sh` to interpret — which means any part of that string built from a filename, a stream name, or anything else that could ever come from outside your own code becomes a place where someone could inject extra shell commands. Building the argument list explicitly, as Python objects, means there's no shell parsing anything, ever. You'll see this exact rule again — worded almost identically — when you build the real pipeline's argv in a later lesson.

### `subprocess.Popen` vs. `subprocess.run`

You met `subprocess` for the first time in this lesson, so it's worth being precise: `subprocess.run(...)` is a convenience wrapper that starts a process **and blocks until it finishes**, in one call — fine when you just need to run something and get its result. `Popen` is the lower-level building block underneath it: it starts the process and returns immediately, handing you a live handle (`_current_proc`) you can inspect, signal, or wait on, on your own schedule. The supervisor needs `Popen` specifically because it must be able to reach into `_current_proc` from the signal handler — `.terminate()` — while `.wait()` is still blocking in the main loop. `subprocess.run` gives you no handle to reach for while it's running.

### The signal handler runs *between* your other code, asynchronously

`_request_shutdown` doesn't get called by you — the Python interpreter calls it, at some point after the OS delivers the signal, interrupting whatever the main thread was doing. That's why it's kept deliberately tiny: set a flag, and if a child is currently running, forward the signal to it. It does not itself wait for the child to finish, print a final summary, or do anything else that takes time — all of that stays in `main()`, driven by the flag the handler set. Signal handlers that try to do too much are a classic source of subtle bugs; the discipline here is "the handler only ever changes shared state, the main loop is what acts on it."

### Reading the return code correctly

`_current_proc.returncode` after `.wait()` tells you exactly how the child ended, and Python encodes two different situations in the same integer:

- **Zero or a positive number** → the child ran to completion and called `exit(N)` itself. `0` means success; anything else is that program's own error code.
- **A negative number** → the child was terminated *by a signal*, and the number is `-signal_number`. A child killed by `SIGTERM` (signal 15) shows up as `returncode == -15`; killed by `SIGKILL` (signal 9), `returncode == -9`.

This is not a Python quirk you need to memorize forever — it's exposing exactly what the underlying `wait()` system call reports, because the two situations genuinely are different events (a program deciding to stop, versus a program being stopped from outside), and code that reacts to failures should be able to tell them apart. Your supervisor's log line does exactly that: `"killed by signal N"` versus `"failed (exit N)"`.

## Step 5 — Run it, and watch the two processes together

```bash
python3 looper.py
```

You'll see the supervisor's timestamped log lines interleaved with the child's `[camera] tick N` lines — both are writing to the same terminal, because `Popen` inherits the parent's stdout by default; you didn't have to wire that up. In a second terminal:

```bash
ps aux | grep camera_sim.py
```

That's a real PID, a real OS process — not the fake incrementing integer from Lesson 1's `_next_pid`. This is what a real `managed: true` state looks like underneath.

## Step 6 — Graceful shutdown, twice over

**From the terminal running `looper.py`**, press `Ctrl+C`. You should see, in order: the supervisor logs `received SIGINT, shutting down`, the child logs its own `SIGTERM received, exiting cleanly`, the supervisor logs the loop stopped, then `stopped`, and control returns to your prompt. Confirm with `ps aux | grep camera_sim.py` in the other terminal — nothing there.

Now do the equivalent **from outside** — this is the scenario that matters most, because it's exactly what your future FastAPI backend will do when the Stop button (Lesson 1) controls a real process instead of a fake dict. Start `looper.py` again, and from the second terminal:

```bash
kill -TERM <looper's pid>
```

Same clean sequence. `Ctrl+C` and an external `kill -TERM` produce identical behavior here on purpose — your handler treats `SIGINT` and `SIGTERM` the same way, because from the supervisor's point of view, "the person at this keyboard wants it stopped" and "some other process wants it stopped" deserve the same graceful response.

## Step 7 — Rehearsing a crash

Trigger the escape hatch from Step 2:

```bash
CRASH_AFTER=2 python3 looper.py
```

Watch the log. The child ticks twice, prints `simulated crash`, and exits with code `1`. The supervisor should log `failed (exit 1)`, back off `1s`, restart — and crash again two seconds later, this time backing off `2s`, then `4s`, doubling each time up to the `30s` cap. Let it run through a few cycles, then `Ctrl+C` to stop. This is the exact mechanism the real project's spec requires: *"Non-zero exit → exponential backoff, 1s → 30s cap, then keep retrying. A network blip must not kill the agent."* You just built and watched that requirement, against a fake failure instead of a real network blip — the code doesn't know or care which.

## Step 8 — The failure mode you cannot code your way out of

This one you should deliberately cause, not just read about. Start `looper.py` again, then from the second terminal, find and **directly kill the child**, not the supervisor:

```bash
ps aux | grep camera_sim.py     # note its pid
kill -KILL <that pid>
```

Watch the supervisor's log: it correctly detects `killed by signal 9` and restarts a fresh child, because it was the *child* that died — the supervisor's own `.wait()` simply unblocked, same as any other exit. Good, expected behavior.

Now try the more dangerous version. Start `looper.py` fresh, and this time **kill the supervisor itself with `-KILL`**, not `-TERM`:

```bash
ps aux | grep looper.py         # note its pid
kill -KILL <looper's pid>
```

Check `ps aux | grep camera_sim.py` afterward. **The child is still running** — orphaned, with no supervisor left to know it exists, let alone stop it. This is not a bug in the code above; it's the direct, unavoidable consequence of Step 3's table — `SIGKILL` cannot be caught, so `_request_shutdown` never runs, so `.terminate()` on the child never gets called. Nothing you write in Python can prevent this, because by the time your process receives a `SIGKILL`, it doesn't get to run any more of your code at all.

Clean up the orphan by hand: `kill -TERM <the orphaned camera_sim pid>`.

**Why this is worth knowing now, precisely:** the real project's spec flags an almost identical failure mode for the Docker-based version of this same supervisor — stopping the `docker run` client process doesn't stop the container it started, because the container is a child of the Docker daemon, not of that client. Same shape of bug, same root cause (a process disappearing without a chance to clean up after itself), different mechanism. You now recognize it on sight.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Ctrl+C` seems to do nothing for a second, then works | Normal — the handler sets a flag; the child needs a moment to notice `_stop` and print its own exit line. Not a hang. |
| Child keeps running after the supervisor exits | You used `-KILL` on the supervisor (Step 8), or the supervisor crashed with an unhandled exception before reaching its shutdown logic. `-TERM`/`Ctrl+C` are the only signals it can act on. |
| `returncode` is a large positive number instead of negative after a `kill -KILL` | You're on a platform where signal-based exits are reported differently (rare outside POSIX) — this lesson assumes macOS/Linux. |
| Backoff never resets to `1s` | Check that the `if returncode == 0` branch (clean exit) resets `backoff = BACKOFF_START` — only failures should grow it. |
| Two `[camera]` tick streams interleaved oddly | You have two `looper.py` instances running from different terminals — check `ps aux | grep looper.py` and stop the extra one. |

### Recap

- A child process, once spawned with `Popen`, keeps running independently of your script — nothing stops it automatically when your script exits.
- `SIGINT` and `SIGTERM` can be caught and handled in Python; `SIGKILL` cannot, ever, by design.
- A signal handler should stay small — set a flag and forward the signal if needed — and let the main loop, which has full context, do the actual work of responding.
- `subprocess.Popen` (not `subprocess.run`) is what you need whenever you must retain a live handle to a child while something else — a signal, an HTTP request — might need to act on it mid-run.
- A negative `returncode` means "killed by a signal"; encode that distinction in your logs, don't collapse it into a generic failure.
- Exponential backoff (starting small, doubling, capped) is what keeps a supervisor from hammering a resource that's already failing, while still recovering promptly from a one-off blip.
- Some failure modes — an orphaned child after a `SIGKILL` to its supervisor — cannot be prevented in code. Recognizing that in advance is part of the design, not a gap in it.

### Exercises

1. Add an escalation timeout: if `.terminate()` doesn't cause the child to exit within 5 seconds, call `_current_proc.kill()` (which sends `SIGKILL`) as a last resort. You'll need `_current_proc.wait(timeout=5)` wrapped in a `try`/`except subprocess.TimeoutExpired`. Test it by editing `camera_sim.py`'s handler to `time.sleep(20)` before exiting, and confirming your supervisor escalates instead of hanging forever.
2. Change `camera_sim.py` so it exits(0) on its own after 10 ticks (simulating a finite clip reaching its end, like the real project's looping video file) instead of running forever. Confirm the supervisor's "exited cleanly, restarting" path relaunches it immediately, with no backoff — and that this now runs indefinitely without ever needing a crash to keep looping.
3. Add a log line, once per loop, recording the *wall-clock* start and end time (not just duration) — `datetime.now()`, not `time.monotonic()` — matching the real spec's requirement: `"2026-08-28 14:03:11  loop 47 started"`.
4. In one or two sentences: why does `_request_shutdown` check `_current_proc.poll() is None` before calling `.terminate()`, instead of just always calling it?

### Where this is going

Compare this lesson's `_current_proc` to Lesson 1's `_state["pid"]`. They're the same idea — a handle to a running process this server is responsible for — except one is now real. When you eventually build the real `server/recording.py`, its `start()` will call something very close to `subprocess.Popen(...)` and store the handle exactly the way `_current_proc` is stored here; its `stop()` will send `SIGTERM`, wait up to 15 seconds, and escalate to `SIGKILL` if the process hasn't exited — precisely Exercise 1, above, with a longer timeout. The FastAPI layer and the process-supervision layer you've now built separately are about to become one system.

---

## Part B — Wiring It Together: A Real Process Behind the Recording Button


Two lessons have been building toward this moment from opposite directions. Lesson 1 built `GET/POST /api/recording` against a fake `_state` dict with a made-up `pid`. Part A built a real supervised process — `subprocess.Popen`, real signals, a real `pid` — with no web framework anywhere near it. This lesson deletes the fake dict and replaces it with the real thing. When it's done, clicking a button in a browser will start an actual OS process, and the status text on the page will reflect what's actually running, not what the page hopes is running.

This is also where the real project's structure stops being an abstraction. You are about to write something extremely close to the actual `server/recording.py`, `server/app.py`'s recording routes, and a first pass at `web/index.html` / `web/app.js` — not a simplified stand-in for them.
## Step 9 — Project layout

New project folder, separate from the previous two:

```
recording-app/
├── camera_sim.py     # from Part A, unchanged
├── recording.py       # NEW — owns the one real piece of server state
├── main.py             # FastAPI routes, thin
└── web/
    ├── index.html
    └── app.js
```

Copy `camera_sim.py` from Part A as-is — it already does everything needed: ticks forever, exits cleanly on `SIGTERM`. No changes to it in this lesson.

## Step 10 — `recording.py`: the one place that touches the process

This module plays the same role as the real project's `server/recording.py`: it is the *only* code that knows a subprocess exists. Routes in `main.py` will call three functions — `status()`, `start()`, `stop()` — and never touch `subprocess` directly.

Start with `status()` and `start()`, reusing Part A's ideas directly:

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
        return current                              # idempotent — Lesson 1's rule, for real now
    _current_proc = subprocess.Popen([sys.executable, CHILD_SCRIPT])
    return status()
```

`_reap_if_dead` is new: Part A's supervisor found out about a dead child because it was sitting in a blocking `.wait()` call. Here, nothing is blocking — a web request could arrive at any time, long after the child crashed on its own. `poll()` — new in this lesson — asks "has this process exited?" *without* blocking, returning `None` if it's still running or the exit code if it's not. Calling this at the top of `status()` means a crash between requests is discovered on the very next status check, not left showing a stale `running: true`.

## Step 11 — Detecting a process you didn't start (and a bug worth causing on purpose)

Lesson 1's `_simulate_external_start` faked the `managed: false` case with a debug route. Now that the process is real, you can detect it for real — but this is exactly where the real spec warns about a subtle trap (`pgrep -f` matching a shell wrapper instead of the real process), and it's worth understanding *why* rather than just avoiding the syntax it names.

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
        if len(tokens) < 2 or os.path.basename(tokens[1]) != CHILD_SCRIPT \
                or not os.path.basename(tokens[0]).startswith("python"):
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

`tokens[1]` is "whatever comes right after the interpreter" — for a process started as `python3 camera_sim.py`, that's `tokens[0] == "python3"` and `tokens[1] == "camera_sim.py"`, exactly the shape you're looking for. `os.path.basename(...)` handles the case where someone ran it as `python3 ./camera_sim.py` or with a full path. Nothing about *mentioning* the file in a longer command line produces that shape. **Editing and viewing it, though, do** — `vim camera_sim.py` and `less camera_sim.py` are two tokens with the script second, exactly like `python3 camera_sim.py`, which is why the check also asks that the token *before* the script be a `python*` binary. The first draft of this lesson checked position alone and claimed it ruled editors out; the test in [`kvsvms/tests/test_recording.py`](./kvsvms/tests/test_recording.py) put `vim`, `less`, `tail -f` and `grep` in a fake process table and found `vim` immediately. Position *and* interpreter is what the rule actually needs.

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

Kill the external process from Step 11 for now (`kill -TERM` its pid) — you'll bring it back deliberately in Step 14's test walkthrough.

## Step 12 — `stop()`: refuse, or terminate-then-escalate

```python
class NotManaged(Exception):
    """Raised when the running recording wasn't started by this server."""


def stop():
    global _current_proc
    current = status()
    if not current["running"]:
        return current                     # idempotent no-op, same as Lesson 1
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

This is Exercise 1 from Lesson 1, and Exercise 1 from Part A, now actually implemented: a `409`-worthy refusal when the process isn't yours, and the real 15-second `SIGTERM`-then-`SIGKILL` policy from the spec. `NotManaged` is a plain Python exception, not an HTTP concept — deciding it means a `409` is `main.py`'s job, not this module's. Keeping that translation out of `recording.py` is what makes this module testable on its own, exactly as you did with plain function calls in Lesson 1 and Part A, with no server running at all.

## Step 13 — One more real-world hazard: your own terminal

Start a recording (call `start()` from a `python3 -c` one-liner, or wait until Step 14 to do it through the API) and think about what happens when you later press `Ctrl+C` on the terminal running `uvicorn`. A terminal's `Ctrl+C` doesn't target one process — it sends `SIGINT` to the **entire foreground process group**. Unless told otherwise, a child spawned by `Popen` inherits its parent's process group, which means stopping your server this way sends `SIGINT` straight to the recording process too — simultaneously, out from under your own careful `stop()` logic, which never gets to run.

Whether that's catastrophic depends on luck: `camera_sim.py` only has a handler for `SIGTERM`, not `SIGINT`, so an unhandled `SIGINT` triggers Python's default behavior — an unhandled `KeyboardInterrupt`, a traceback, and exit. Untidy, but the process does stop. The real risk is the opposite mistake going unnoticed for a long time: code that *assumes* the child only ever stops through your own `stop()` function, when in fact a keystroke in the wrong terminal can kill it a different way entirely.

Fix it by giving the child its own process group:

```python
_current_proc = subprocess.Popen(
    [sys.executable, CHILD_SCRIPT],
    start_new_session=True,
)
```

`start_new_session=True` puts the child in a new session and process group of its own. A `Ctrl+C` in the terminal running your server now reaches only the server — the recording process is untouched, and stops only when your own `stop()` explicitly signals it. Update `start()` to include this argument.

> This one is worth more than reading about — Step 14 has you cause the *without* case on purpose and watch it happen, then confirm the fix.

## Step 14 — `main.py`: thin routes over `recording.py`

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
- `app.mount(...)` is declared **last**, after the API routes. This is Lesson 1's route-ordering lesson again, one level up: a mount is matched in the order it's registered too, and a mount at `"/"` would happily swallow `/api/recording` if it were declared first. Explicit routes before a catch-all — same rule, new context.

`fastapi.staticfiles.StaticFiles` is new here: `directory="web", html=True` means requests to `/` serve `web/index.html`, and requests to `/app.js` serve `web/app.js` — a plain file server for anything that isn't one of your explicit API routes.

## Step 15 — The page and the button

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

This page is deliberately bare — no timeline, no video, none of the visual design work the real frontend module covers later. It exists to prove the wiring works, the same way Lesson 1 tested through `/docs` instead of a real page.

## Step 16 — Full test walkthrough

Run `uvicorn main:app --reload` and open `http://127.0.0.1:8000/`. Work through this exact sequence — it exercises every piece built above, in the order most likely to reveal a mistake:

1. **Start via the button.** Status flips to `● recording`, button becomes `Stop`. Confirm in a second terminal: `ps -eo pid,args | grep camera_sim.py` shows exactly one real process, whose pid matches what `GET /api/recording` reports.
2. **Click Start again** (or refresh and click before the first request would plausibly have failed). Still exactly one `camera_sim.py` process — idempotency, holding under a real process this time, not just a dict.
3. **Stop via the button.** Status flips back, the process disappears from `ps`.
4. **Simulate a teammate.** In the second terminal: `python3 camera_sim.py &`. Wait up to 3 seconds for the page to poll. Status shows `● recording` — detected via `_find_external_camera_pid`, not started by you. Hover the button: the tooltip explains it isn't yours to stop. Click **Stop** anyway: the page shows the `409`'s message instead of silently failing or crashing. Confirm via `ps` that the external process is untouched. Clean it up by hand: `kill -TERM` its pid in the second terminal.
5. **Cause Step 13's hazard, then fix it.** Temporarily remove `start_new_session=True` from `start()`, restart `uvicorn`, click Start, then press `Ctrl+C` on the terminal running `uvicorn`. Check `ps` — the recording process is gone too, killed by the same `Ctrl+C`, without your `stop()` logic ever running. Put `start_new_session=True` back, restart, repeat: click Start, `Ctrl+C` the server, check `ps` again — this time the recording process is still running, orphaned but alive, exactly as designed.
6. **The honest limitation.** With the orphaned process from step 5 still running, start `uvicorn` again and reload the page. Status shows `● recording` — but now `managed: false`, and the Stop button will `409` if you press it. The *new* server process has no memory of ever starting that recording; only real, ongoing process ownership survives a restart, not the fact of who originally started it. This isn't a bug to fix — it's a direct, honest consequence of tracking ownership in memory, the same limitation Lesson 1 and Part A both flagged for their own state. Stop the orphan by hand: `kill -TERM` its pid.

If every step above matches what's described, the web layer and the edge layer are now one working system.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ModuleNotFoundError: No module named 'recording'` | `main.py` and `recording.py` must be in the same folder, and you must run `uvicorn` from that folder. |
| `/` returns a raw file listing or a 404 instead of the page | Check `app.mount("/", StaticFiles(directory="web", html=True), ...)` is the *last* thing registered, and that `web/index.html` exists relative to where you run `uvicorn`. |
| Clicking Start does nothing visible | Open the browser's developer console for a JS error before assuming the backend is at fault — a typo in `app.js` fails silently in the page, not in your terminal. |
| Two `camera_sim.py` processes after clicking Start once | `start()` is missing its idempotency check (`if current["running"]: return current`) — re-check Step 10. |
| External-agent detection finds nothing even though `camera_sim.py &` is running | Confirm you're testing with the *positional* version from Step 11, not the naive one — and that you started it as `python3 camera_sim.py`, not via a wrapper script whose argv looks different. |
| `Stop` always 409s, even for a recording you started via the button | Check `start()` is actually storing the handle in `_current_proc` (a stray local variable instead of the module-level one is a common typo) — `status()` should show `managed: true` right after a button-driven start. |

### Recap

- `recording.py` is the single place that owns the real process handle — routes in `main.py` never touch `subprocess` directly, mirroring the real project's design intent.
- `poll()` is the non-blocking way to notice a child has exited between requests, versus Part A's blocking `.wait()` inside a dedicated supervisor loop.
- Detecting "a process by name" via `ps` must check *position* (the script name immediately after the interpreter), not mere *presence* of the name anywhere in the command line — the same category of bug as `ps | grep` matching itself, and exactly why the real spec forbids `pgrep -f` for this.
- `terminate()` → `wait(timeout=...)` → `kill()` on `TimeoutExpired` is the real stop policy: ask nicely, then force it.
- `start_new_session=True` isolates a managed child from signals sent to the server's own process group — without it, stopping the server can silently kill (or corrupt the shutdown of) the process it's supposed to be managing separately.
- A page should reflect server-reported state after every action, never its own optimistic assumption about what just happened.
- Process *ownership* tracked only in memory does not survive a server restart, even though the process itself might — a real, unavoidable limitation, not a bug.

### Exercises

1. Add a `GET /api/recording/log` route (or similar) that tails the last N lines of the child's output — you'll need to redirect `camera_sim.py`'s stdout to a file in `start()` (`stdout=open("camera.log", "a")`) rather than letting it inherit the server's own stdout, since a real deployment can't assume someone is watching the terminal.
2. The naive substring matcher from Step 11 would also misfire if a student ever names an unrelated script `my_camera_sim.py.bak` — walk through *why* the positional fix handles that case correctly too, without changing a line.
3. Right now, restarting the server always demotes a previously self-started, still-running recording to `managed: false` (Step 16.6). Sketch, in comments, one way you could make ownership survive a restart (hint: what would you need to write to disk, and when, to reconstruct it later — and what new failure modes would that persistence itself introduce?). You don't need to implement it — the real spec deliberately keeps this in-memory-only; explain why that might be the right trade-off rather than an oversight.
4. Add a `RESTART_DELAY` constant and a `POST /api/recording/restart` route that calls `stop()` then `start()` — decide and justify: should this be idempotent the same way `start()` and `stop()` are individually, and what should happen if it's called while a `409`-worthy external recording is running?

## Where this is going

`camera_sim.py` has done its job — it let you build and test every piece of process ownership, signal handling, and web wiring without needing GStreamer, `kvssink`, or AWS credentials installed. Lessons 3 and 4 swap it for the real pipeline from the project spec (`gst-launch-1.0 ... kvssink ...`) and introduces `boto3` for the two read endpoints (`GET /api/fragments`, `GET /api/hls`) that make the archive browsable — `recording.py`, `main.py`, and `app.js` above barely change shape when that happens; they gain neighbors, not replacements.
