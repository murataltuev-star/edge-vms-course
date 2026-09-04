# Lesson 8 — Dockerizing the Edge Agent

**Module:** SDKs & Containers (Module 4)
**You will build:** a `KVS_DOCKER_IMAGE` toggle for Lesson 5's `looper.py` that runs the child in a container instead of as a plain subprocess — and understand, precisely, why a container needs a different kind of care when stopping it than a plain process does.
**Time:** ~75–90 minutes.

## Why this lesson exists

Lesson 5 built a supervisor for a plain subprocess. Lesson 7 showed how to containerize that same script. This lesson does the thing both were building toward: `looper.py` gains a single configuration toggle that switches its child from "a plain OS process" to "a Docker container," with almost no other code changing. Almost — because containers introduce one genuinely new failure mode that plain subprocesses don't have, and understanding it precisely is the actual point of this lesson, not a footnote.

This mirrors the real project's design directly: the reference spec's `KVS_DOCKER_IMAGE` environment variable does exactly this — empty means run `kvssink` on the host, set it to an image name and both `edge/looper.py` and `scripts/check_env.py` switch to container mode "with no other changes." You're building that toggle for real, against `camera_sim.py` instead of the real GStreamer pipeline, exactly as Lessons 5 and 6 used it as a stand-in for the same reason.

## Prerequisites

- Lesson 5 (the original `looper.py` and `camera_sim.py`) and Lesson 7 (Docker fundamentals, the `camera-sim` image, and its `--name` collision behavior from Exercise 4) both completed. This lesson assumes you can explain why `SIGKILL` can't be caught (Lesson 5, Step 3) — that fact reappears here, one layer up.

## Learning objectives

1. Wrap an existing subprocess call in `docker run` behind a single config toggle, without changing the surrounding supervisor logic.
2. Explain precisely why killing the `docker run` client process does not necessarily stop the container it started — and when it actually does.
3. Use a deterministic container name plus `docker rm -f`, run *before* every launch as well as on shutdown, to keep the supervisor's restart loop from failing forever.
4. Forward credentials into a container by variable name only, so a secret's value never appears in a process's own command line.
5. Read a non-trivial `Dockerfile` (the one behind `kvssink`) and explain what problem each of its non-default choices solves.

---

## Step 1 — One toggle, one small function

Everything in Lesson 5's `looper.py` stays as-is. Add a single environment-driven switch:

```python
import os

CHILD_SCRIPT = "camera_sim.py"
DOCKER_IMAGE = os.environ.get("KVS_DOCKER_IMAGE")   # unset/empty = host mode, exactly Lesson 5
STREAM_NAME = "cam-01"
CONTAINER_NAME = f"kvs-vms-edge-{STREAM_NAME}"


def _build_argv():
    if not DOCKER_IMAGE:
        return [sys.executable, CHILD_SCRIPT]
    return ["docker", "run", "--name", CONTAINER_NAME, DOCKER_IMAGE]
```

That's the entire "does this run in Docker" decision, in one function. Everything downstream — `subprocess.Popen(argv)`, `.wait()`, reading `.returncode` — is unchanged from Lesson 5, because a `docker run` invocation *is* just another command with an argv list, blocking until it exits, exactly like `python3 camera_sim.py` was. `CONTAINER_NAME` reuses `camera-sim`, the image you already built in Lesson 7.

Build the image again if you haven't kept it: `docker build -t camera-sim .` in your Lesson 7 folder.

## Step 2 — Run it, and confirm the easy case works for free

Set the toggle and start the supervisor:

```bash
KVS_DOCKER_IMAGE=camera-sim python3 looper.py
```

In a second terminal, `docker ps` should show `kvs-vms-edge-cam-01` running, and `looper.py`'s own terminal should be showing `[camera] tick N` lines — the container's stdout, attached to the `docker run` client (foreground, no `-d`), attached in turn to `looper.py`'s own inherited stdout, exactly the chain Lesson 5 relied on for the plain-subprocess case.

Now press `Ctrl+C`. Watch it shut down cleanly — same log lines as Lesson 5, same clean exit. Confirm with `docker ps` in the second terminal: no container running. **Nothing about your shutdown-handling code changed, and it still works.** Here's why, precisely: `_request_shutdown` calls `_current_proc.terminate()`, which sends `SIGTERM` to `_current_proc` — but in Docker mode, `_current_proc` is the `docker run` *client* process, not the container. Docker's client has a feature called signal-proxying, on by default: a signal the client process itself receives and gets the chance to handle, it forwards into the container. `camera_sim.py` already has a `SIGTERM` handler (Lesson 5), so it exits cleanly inside the container, the container stops, the attached client sees that and exits too, and your `.wait()` unblocks — the entire chain nobody had to build.

## Step 3 — Reproduce the failure mode this lesson is really about

That phrase above — "a signal the client process itself receives and gets the chance to handle" — is the load-bearing part. Cause the case where that doesn't happen, on purpose.

Start the supervisor again the same way. In the second terminal, find the **client's** pid — not the container's:

```bash
ps aux | grep "docker run"
```

Send it `SIGKILL`, not `SIGTERM`:

```bash
kill -9 <that pid>
```

Watch `looper.py`'s log: its `.wait()` unblocks almost immediately (its direct child, the client process, just died), and it logs something like `killed by signal 9`. Now check `docker ps` in the second terminal. **The container is still there, still running.** `looper.py` believes the recording stopped — its own log says so — but it didn't.

This is the exact scenario Lesson 5's Step 3 table warned about, one layer higher up: `SIGKILL` cannot be caught, by anything, ever. The `docker run` client's signal-proxying feature is application-level code *inside* the client — code that only runs if the client is given the chance to run its own signal handler. A `SIGKILL`'d process never runs another line of its own code, so there is no proxying, no forwarding, nothing. And separately from that: the container was never the client's child in the OS process-tree sense to begin with — it's a process the **Docker daemon** launched and continues to own, with the client acting only as a messenger for as long as it's alive. Kill the messenger, and the thing it was relaying to doesn't hear about it — not because the message was slow, but because there was never a direct line between them in the first place.

Confirm the orphan is real and unaffected: wait a few seconds, check `docker logs kvs-vms-edge-cam-01` — ticks are still incrementing, the process never stopped.

## Step 4 — Why the pre-loop cleanup isn't optional

Leave that orphan running, and let `looper.py` (which is still executing — it thinks the loop ended, and per its own backoff logic will try to restart) attempt its next iteration. Watch the log. Without any extra code, the *next* `docker run --name kvs-vms-edge-cam-01 camera-sim` will fail outright:

```
docker: Error response from daemon: Conflict. The container name "/kvs-vms-edge-cam-01" is already in use...
```

— exactly the error you deliberately caused in Lesson 7's Exercise 4, now happening automatically, forever, on every retry, because the orphan from Step 3 is still holding that name. Fix it the way the real spec requires: remove any container by that name **before every launch**, not only on shutdown.

```python
def _remove_stale_container():
    """`docker run --name` fails outright if a container of that name still
    exists, running or merely stopped -- an orphan left by Step 3 would
    otherwise fail every subsequent restart forever. This is not redundant
    with the shutdown cleanup below; it runs before every single iteration."""
    if DOCKER_IMAGE:
        subprocess.run(
            ["docker", "rm", "-f", CONTAINER_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
```

Call it at the top of `run_child_once()`, before building the argv, and once more in a `finally` block wrapped around `main()`'s loop, so a clean shutdown also guarantees no container survives the supervisor itself:

```python
def run_child_once():
    global _current_proc
    _remove_stale_container()
    argv = _build_argv()
    _current_proc = subprocess.Popen(argv)
    if _shutting_down:
        _current_proc.terminate()
    started = time.monotonic()
    _current_proc.wait()
    duration = time.monotonic() - started
    returncode = _current_proc.returncode
    _current_proc = None
    return returncode, duration


def main():
    backoff = BACKOFF_START
    loop_num = 0
    try:
        while not _shutting_down:
            loop_num += 1
            label = f" (docker image {DOCKER_IMAGE})" if DOCKER_IMAGE else ""
            _log(f"loop {loop_num} started{label}")
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
    finally:
        _remove_stale_container()
        _log("stopped")
```

`docker rm -f` talks directly to the Docker **daemon** — not to any client process, alive or dead — which is exactly why it's the right tool here: it doesn't depend on anything being alive to proxy a signal through. It works whether the target container is running, orphaned, or already stopped, which is what makes it safe to run unconditionally before every single iteration rather than only when you suspect a problem.

Repeat Step 3's `kill -9` test with this version. `looper.py` should log the same `killed by signal 9`, but the *next* loop iteration should now succeed cleanly — `docker ps` briefly shows no container (removed), then a fresh one under the same name.

## Step 5 — Credentials never belong in an argv

The real edge agent needs AWS credentials inside the container. The tempting way to pass them:

```python
return ["docker", "run", "--name", CONTAINER_NAME,
        "-e", f"AWS_SECRET_ACCESS_KEY={os.environ['AWS_SECRET_ACCESS_KEY']}",
        DOCKER_IMAGE]
```

Don't do this. Whatever you put after `-e KEY=`, value included, becomes part of this process's own command line — and a command line is visible to `ps -eo pid,args`, the exact output Lesson 6 scanned to *find* a process by what's in its arguments. A secret written there is readable by anyone on the machine who can run `ps aux`, or inspect `/proc/<pid>/cmdline` directly.

The fix is a `-e` flag with **no value**:

```python
def _build_argv():
    if not DOCKER_IMAGE:
        return [sys.executable, CHILD_SCRIPT]
    argv = ["docker", "run", "--name", CONTAINER_NAME]
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION"):
        if var in os.environ:
            argv += ["-e", var]        # name only -- no "=value" here, ever
    argv.append(DOCKER_IMAGE)
    return argv
```

`-e VAR` with a bare name tells Docker "read this variable's *value* from my own environment, and set the same variable inside the container" — the value is passed through the daemon's API call, never typed into this argv list at all. Confirm it: export a dummy value, start the supervisor in Docker mode, and in the second terminal run `ps -eo pid,args | grep "docker run"`. You'll see `-e AWS_ACCESS_KEY_ID` in the output — the bare name, never the value.

(The real spec forwards `AWS_SESSION_TOKEN` this same way unconditionally, relying on the fact that Docker simply omits a variable that was never set in the parent's environment rather than erroring — a small simplification over checking `in os.environ` first, equally safe either way.)

## Step 6 — Reading material: the real `kvssink` Dockerfile

You are not building this image in this lesson — per the reference spec, it's a genuine 20–40 minute compile of a C++ SDK from source, wants real memory headroom, and has failure modes that have nothing to do with anything you've learned so far. What follows is the Dockerfile that build produces, annotated against the reference spec's own build notes, so that when Module 5 hands you `gst-launch-1.0` and a pipeline that includes `kvssink`, you already understand where that element came from and why it doesn't just `apt install`.

```dockerfile
# Two departures from AWS's own sample Dockerfile are load-bearing here --
# see the annotations below before "simplifying" either one back.

FROM ubuntu:22.04
# NOT amazonlinux:2. That image's GCC (7.3.1) OOM-kills the compiler (cc1plus)
# partway through building one of the dependencies below, even with plenty of
# host memory free. The failure ("cannot allocate memory") reads exactly like
# a resource limit -- it is actually a toolchain problem, and costs real time
# to diagnose the first time you hit it. Ubuntu 22.04's newer GCC doesn't.

RUN apt-get update && apt-get install -y \
    build-essential cmake git pkg-config \
    libssl-dev liblog4cplus-dev libcurl4-openssl-dev \
    gstreamer1.0-tools gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    x264 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# log4cplus / OpenSSL / curl come from apt here, matched by
# -DBUILD_DEPENDENCIES=OFF below: the dependency that would otherwise need to
# be compiled from source inside this image is linked from a package instead.
# x264 + ffmpeg: lets this image generate a synthetic test clip when the host
# has none -- the alternative is requiring a full media toolchain on every
# machine just to produce a 60-second test file.

ARG BUILD_JOBS=2
# NOT $(nproc). Docker Desktop reports every host CPU core to the container
# while actually granting only a fraction of host RAM -- budget roughly
# 1.5GB per parallel compile job, or `make -j` OOMs for the same underlying
# reason the wrong base image did above, just later in the build.

RUN git clone --depth 1 \
      https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp.git \
      /opt/kvs-sdk \
    && mkdir -p /opt/kvs-sdk/build \
    && cd /opt/kvs-sdk/build \
    && cmake .. -DBUILD_GSTREAMER_PLUGIN=ON -DBUILD_DEPENDENCIES=OFF \
    && make -j"${BUILD_JOBS}"

ENV GST_PLUGIN_PATH=/opt/kvs-sdk/build
# kvssink is not part of stock GStreamer -- this is the line that makes
# `gst-inspect-1.0 kvssink` (and the real pipeline's own kvssink element)
# resolve at all, inside a container built from this image.

WORKDIR /app
ENTRYPOINT ["gst-launch-1.0", "-q"]
```

Read the `ENTRYPOINT` line as a preview, not a mystery to solve now — Module 5 is entirely about what `gst-launch-1.0` is and how to read (and write) the pipeline that follows it. For this lesson, the point is narrower and already familiar: this `Dockerfile` exists, with every one of its non-default choices deliberate, for exactly the reason Step 1 of Lesson 7 introduced in the abstract — a compiled dependency with real, OS-specific failure modes gets built once, correctly, and shipped as an image, so nobody downstream repeats the compile (or its failures) themselves.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `docker: Error response from daemon: Conflict. The container name ... is already in use` | `_remove_stale_container()` is missing from the top of `run_child_once()`, or only runs on shutdown — it must run before every iteration. |
| Killing the client with `kill -9` doesn't leave an orphan on your machine | Check you found the **client's** pid (`ps aux | grep "docker run"`), not the container's own process — killing the wrong one won't demonstrate anything. |
| `docker ps` still shows the container after a clean `Ctrl+C` | Confirm `camera_sim.py` inside the image still has its `SIGTERM` handler from Lesson 5 — without it, the proxied signal still stops the container (default action for an unhandled `SIGTERM` is termination), but you won't see the "exiting cleanly" log line. |
| Credentials still visible in `ps` output | Double check you're passing `-e VAR` with no `=value` — a single leftover `-e VAR=value` anywhere in `_build_argv` defeats the whole point. |
| `cc1plus` killed / `cannot allocate memory` while reading Step 6 | This is the exact base-image gotcha the annotations describe — it's why the Dockerfile uses `ubuntu:22.04` and caps `BUILD_JOBS`, not a sign anything here is wrong. |

## Recap

- Wrapping a child in `docker run` needed only one new function (`_build_argv`) — every other line of the supervisor from Lesson 5 was already generic enough to not care what it was supervising.
- `docker run`'s client proxies signals it receives (and gets to handle) into the container it started — which is why a plain `SIGTERM`/`Ctrl+C` shutdown works with zero extra code.
- That proxying is application-level code inside the client; a `SIGKILL`'d client never runs it, and the container — which the Docker daemon owns, not the client — is left running, orphaned. Same uncatchable-signal fact from Lesson 5, one layer up.
- A deterministic container name plus `docker rm -f`, run before every launch (not only on shutdown), is what keeps an orphan from turning into a permanent restart failure.
- `-e VAR` with no value forwards a credential's value through the Docker daemon directly; `-e VAR=value` leaks it into a process's own command line, visible to anyone who can run `ps`.
- A compiled SDK with real build failure modes (like the one behind `kvssink`) gets built once, deliberately, into an image — every non-default choice in that `Dockerfile` is answering a specific failure the defaults would otherwise hit.

## Exercises

1. Add a `docker inspect --format '{{.State.Status}}' kvs-vms-edge-cam-01` check to `_remove_stale_container` that logs whether it actually found (and removed) a stale container, versus there being nothing to clean up — useful for confirming Step 4's fix is doing something on a given run, not just assumed to be.
2. The real spec bind-mounts a clip's directory read-only at `/media` inside the container (Lesson 7, Step 4) and rewrites the `location=` path accordingly. Extend `_build_argv` to add `-v` for a local `media/` folder, and change `camera_sim.py` (or reuse `labeled_camera.py` from Lesson 7) to read something from it — confirm the mounted content is visible inside the container the same way it was in Lesson 7's standalone test.
3. `docker run`'s signal-proxying is a convenience, not a guarantee your code should quietly depend on forever. Sketch (comments are enough) how `run_child_once` could explicitly call `docker stop <name>` — which talks to the daemon directly, the same way `docker rm -f` does — instead of relying on `_current_proc.terminate()` plus proxying, and explain in a sentence why that might be more robust for the real project's Docker mode specifically.
4. In your own words: why does the annotation on `ARG BUILD_JOBS=2` say Docker Desktop "reports every host CPU core... while actually granting only a fraction of host RAM"? What specifically would go wrong if this Dockerfile used `make -j$(nproc)` on a modest laptop instead?

## Where this is going

The `ENTRYPOINT ["gst-launch-1.0", "-q"]` line in Step 6 is where this module hands off to the next one. Module 5 opens `gst-launch-1.0` up properly: what a GStreamer *pipeline* actually is, what each element in the real project's pipeline does (`filesrc`, `qtdemux`, `h264parse`, `identity sync=true`, and finally `kvssink` itself), and why a file read from disk needs deliberate pacing to look like a live camera feed at all. `camera_sim.py` retires there — replaced, at last, by the real thing it was always standing in for.
