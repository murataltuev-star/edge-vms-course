# Lesson 5 — Supervising a Long-Running Process: `edge/looper.py`

**Module:** The Edge Agent — Process Supervision & Signals (Module 2)
**You will build:** a real supervisor process that starts, monitors, restarts on crash, and cleanly stops a second real OS process — no web framework involved.
**Time:** ~75–90 minutes.

## Why this lesson exists

Lesson 4 ended with a `_state` dict pretending to hold a running process — a fake `pid`, a fake `managed` flag. That was deliberate: it let you focus entirely on the web layer. Now we build the thing that was being faked: a real, supervised, long-running child process — the actual shape of the real project's `edge/looper.py`, which supervises the GStreamer pipeline that publishes video.

We're not touching GStreamer, AWS, or `kvssink` yet — those come later. The child process for this lesson is deliberately trivial: **an infinite loop that ticks once a second**, standing in for "a pipeline that runs as long as recording is on." What you're learning here is not about video — it's the general skill of *supervising a process you don't fully control*: starting it, telling if it's still alive, restarting it if it dies unexpectedly, and shutting it down cleanly when asked. That skill transfers unchanged to the real pipeline, and to almost any long-running background job you'll ever run.

## Prerequisites

- Lessons 1–4 completed. This lesson does not use FastAPI or Pydantic at all — it's plain Python and the standard library — but Lesson 4's idea of a `managed` process you are responsible for stopping is exactly what you're about to build for real.
- Comfortable with a second terminal window, since several exercises require sending signals to a running process from outside it.

## Learning objectives

1. Explain the parent/child process relationship and what `subprocess.Popen` actually does.
2. Distinguish `SIGINT`, `SIGTERM`, and `SIGKILL`, and know which ones your code can and cannot intercept.
3. Write a Python signal handler and understand when it runs relative to the rest of your code.
4. Build a supervisor loop that logs each run, restarts a crashed child with exponential backoff, and shuts down cleanly on request.
5. Read a `subprocess` return code correctly — including the "killed by a signal" case, which is not the same as "exited with an error."

---

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

That's a real PID, a real OS process — not the fake incrementing integer from Lesson 4's `_next_pid`. This is what a real `managed: true` state looks like underneath.

## Step 6 — Graceful shutdown, twice over

**From the terminal running `looper.py`**, press `Ctrl+C`. You should see, in order: the supervisor logs `received SIGINT, shutting down`, the child logs its own `SIGTERM received, exiting cleanly`, the supervisor logs the loop stopped, then `stopped`, and control returns to your prompt. Confirm with `ps aux | grep camera_sim.py` in the other terminal — nothing there.

Now do the equivalent **from outside** — this is the scenario that matters most, because it's exactly what your future FastAPI backend will do when the Stop button (Lesson 4) controls a real process instead of a fake dict. Start `looper.py` again, and from the second terminal:

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Ctrl+C` seems to do nothing for a second, then works | Normal — the handler sets a flag; the child needs a moment to notice `_stop` and print its own exit line. Not a hang. |
| Child keeps running after the supervisor exits | You used `-KILL` on the supervisor (Step 8), or the supervisor crashed with an unhandled exception before reaching its shutdown logic. `-TERM`/`Ctrl+C` are the only signals it can act on. |
| `returncode` is a large positive number instead of negative after a `kill -KILL` | You're on a platform where signal-based exits are reported differently (rare outside POSIX) — this lesson assumes macOS/Linux. |
| Backoff never resets to `1s` | Check that the `if returncode == 0` branch (clean exit) resets `backoff = BACKOFF_START` — only failures should grow it. |
| Two `[camera]` tick streams interleaved oddly | You have two `looper.py` instances running from different terminals — check `ps aux | grep looper.py` and stop the extra one. |

## Recap

- A child process, once spawned with `Popen`, keeps running independently of your script — nothing stops it automatically when your script exits.
- `SIGINT` and `SIGTERM` can be caught and handled in Python; `SIGKILL` cannot, ever, by design.
- A signal handler should stay small — set a flag and forward the signal if needed — and let the main loop, which has full context, do the actual work of responding.
- `subprocess.Popen` (not `subprocess.run`) is what you need whenever you must retain a live handle to a child while something else — a signal, an HTTP request — might need to act on it mid-run.
- A negative `returncode` means "killed by a signal"; encode that distinction in your logs, don't collapse it into a generic failure.
- Exponential backoff (starting small, doubling, capped) is what keeps a supervisor from hammering a resource that's already failing, while still recovering promptly from a one-off blip.
- Some failure modes — an orphaned child after a `SIGKILL` to its supervisor — cannot be prevented in code. Recognizing that in advance is part of the design, not a gap in it.

## Exercises

1. Add an escalation timeout: if `.terminate()` doesn't cause the child to exit within 5 seconds, call `_current_proc.kill()` (which sends `SIGKILL`) as a last resort. You'll need `_current_proc.wait(timeout=5)` wrapped in a `try`/`except subprocess.TimeoutExpired`. Test it by editing `camera_sim.py`'s handler to `time.sleep(20)` before exiting, and confirming your supervisor escalates instead of hanging forever.
2. Change `camera_sim.py` so it exits(0) on its own after 10 ticks (simulating a finite clip reaching its end, like the real project's looping video file) instead of running forever. Confirm the supervisor's "exited cleanly, restarting" path relaunches it immediately, with no backoff — and that this now runs indefinitely without ever needing a crash to keep looping.
3. Add a log line, once per loop, recording the *wall-clock* start and end time (not just duration) — `datetime.now()`, not `time.monotonic()` — matching the real spec's requirement: `"2026-08-28 14:03:11  loop 47 started"`.
4. In one or two sentences: why does `_request_shutdown` check `_current_proc.poll() is None` before calling `.terminate()`, instead of just always calling it?

## Where this is going

Compare this lesson's `_current_proc` to Lesson 4's `_state["pid"]`. They're the same idea — a handle to a running process this server is responsible for — except one is now real. When you eventually build the real `server/recording.py`, its `start()` will call something very close to `subprocess.Popen(...)` and store the handle exactly the way `_current_proc` is stored here; its `stop()` will send `SIGTERM`, wait up to 15 seconds, and escalate to `SIGKILL` if the process hasn't exited — precisely Exercise 1, above, with a longer timeout. The FastAPI layer and the process-supervision layer you've now built separately are about to become one system.
